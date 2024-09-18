import os
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, send_file
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from utils.pdf_processor import extract_text_from_pdf
from openai import OpenAI
from sqlalchemy import text
from datetime import datetime, timedelta
import logging
import io
from docx import Document
from templates.cover_letter_format import COVERLETTER_FORMAT
import secrets
from flask_mail import Mail, Message

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['UPLOAD_FOLDER'] = '/tmp'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Database configuration
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Login manager configuration
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Mail configuration
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_USERNAME')
mail = Mail(app)

ALLOWED_EXTENSIONS = {'pdf'}

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    password_hash = db.Column(db.String(255))
    ai_model = db.Column(db.String(20), default='gpt-4o-2024-08-06')
    reset_token = db.Column(db.String(100), unique=True)
    reset_token_expiration = db.Column(db.DateTime)
    submissions = db.relationship('Submission', backref='user', lazy='dynamic')
    resumes = db.relationship('Resume', backref='user', lazy='dynamic')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def delete_account(self):
        Submission.query.filter_by(user_id=self.id).delete()
        Resume.query.filter_by(user_id=self.id).delete()
        db.session.delete(self)
        db.session.commit()

    def generate_reset_token(self):
        self.reset_token = secrets.token_urlsafe(32)
        self.reset_token_expiration = datetime.utcnow() + timedelta(hours=1)
        db.session.commit()

    def verify_reset_token(self, token):
        if token != self.reset_token or self.reset_token_expiration < datetime.utcnow():
            return False
        return True

class Submission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    resume_text = db.Column(db.Text, nullable=False)
    focus_areas = db.Column(db.Text, nullable=False)
    job_description = db.Column(db.Text, nullable=False)
    cover_letter = db.Column(db.Text, nullable=False)
    company_name = db.Column(db.String(255))
    job_title = db.Column(db.String(255))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

class Resume(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    content = db.Column(db.Text, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_company_and_job_title(job_description):
    client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

    prompt = f"""
    Extract the company name and job title from the following job description:
    
    {job_description}
    
    Return the information in the following format:
    Company: [Company Name]
    Job Title: [Job Title]
    """

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{
            "role": "system",
            "content": "You are a helpful assistant that extracts company names and job titles from job descriptions."
        }, {
            "role": "user",
            "content": prompt
        }])

    extracted_info = response.choices[0].message.content
    company_name = ""
    job_title = ""

    for line in extracted_info.split('\n'):
        if line.startswith("Company:"):
            company_name = line.split("Company:")[1].strip()
        elif line.startswith("Job Title:"):
            job_title = line.split("Job Title:")[1].strip()

    logger.info(f"Extracted company name: {company_name}")
    logger.info(f"Extracted job title: {job_title}")

    return company_name, job_title

def generate_cover_letter_suggestion(resume_text, focus_areas, job_description, first_name, last_name, ai_model):
    client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

    company_name, job_title = extract_company_and_job_title(job_description)

    static_prompt = "You are a professional cover letter writer."

    full_prompt = (
        f"{static_prompt}\n\n"
        f"Candidate Name: {first_name} {last_name}\n\n"
        f"Company: {company_name}\n"
        f"Job Title: {job_title}\n\n"
        f"Job Description: {job_description}\n\n"
        f"Cover Letter Format: {COVERLETTER_FORMAT}\n\n"
        f"Focus: {focus_areas}\n\n"
        f"My Resume:\n{resume_text}\n\n"
        f"Things to avoid in the writing:\n"
        "Do not use the phrase 'as advertised'. Do not use the word 'tenure'\n\n"
        "Please generate a cover letter that highlights my fit for this role, includes my name, and matches the format described in Cover Letter Format section."
    )

    response = client.chat.completions.create(
        model=ai_model,
        messages=[{
            "role": "system",
            "content": "You are a helpful cover letter writing assistant."
        }, {
            "role": "user",
            "content": full_prompt
        }])

    cover_letter = response.choices[0].message.content
    return cover_letter, company_name, job_title

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        first_name = request.form.get('first_name')
        last_name = request.form.get('last_name')

        user = User.query.filter_by(username=username).first()
        if user:
            flash('Username already exists')
            return redirect(url_for('register'))

        new_user = User(username=username, email=email, first_name=first_name, last_name=last_name)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')

@app.route('/submit', methods=['GET', 'POST'])
@login_required
def submit():
    logger.info("Entering submit function")

    if request.method == 'POST':
        logger.info("Processing POST request for submission")
        resume_selection = request.form.get('resume_selection')

        if resume_selection and resume_selection != 'new':
            resume = Resume.query.get(resume_selection)
            if resume and resume.user_id == current_user.id:
                resume_text = resume.content
                filename = resume.filename
            else:
                flash('Invalid resume selection')
                return redirect(request.url)
        else:
            if 'resume' not in request.files:
                logger.warning("No file part in the request")
                flash('No file part')
                return redirect(request.url)
            file = request.files['resume']

            if file.filename == '':
                logger.warning("No selected file")
                flash('No selected file')
                return redirect(request.url)

            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(filepath)
                logger.info(f"File saved: {filepath}")

                resume_text = extract_text_from_pdf(filepath)

                new_resume = Resume(filename=filename, content=resume_text, user_id=current_user.id)
                db.session.add(new_resume)
                db.session.commit()

                os.remove(filepath)
                logger.info(f"Temporary file removed: {filepath}")
            else:
                logger.warning("Invalid file type")
                flash('Invalid file type. Please upload a PDF file.')
                return redirect(request.url)

        focus_areas = request.form.get('focus_areas')
        job_description = request.form.get('job_description')

        logger.info("Generating cover letter suggestion")
        cover_letter, company_name, job_title = generate_cover_letter_suggestion(
            resume_text, focus_areas, job_description, current_user.first_name, current_user.last_name, current_user.ai_model)

        logger.info(f"Extracted company name: {company_name}")
        logger.info(f"Extracted job title: {job_title}")

        new_submission = Submission(resume_text=resume_text,
                                    focus_areas=focus_areas,
                                    job_description=job_description,
                                    cover_letter=cover_letter,
                                    company_name=company_name,
                                    job_title=job_title,
                                    user_id=current_user.id)
        db.session.add(new_submission)
        db.session.commit()
        logger.info(f"New submission created: {new_submission.id}")

        return redirect(url_for('result', submission_id=new_submission.id))

    saved_resumes = Resume.query.filter_by(user_id=current_user.id).order_by(Resume.created_at.desc()).all()
    return render_template('submit.html', saved_resumes=saved_resumes)

@app.route('/result/<int:submission_id>')
@login_required
def result(submission_id):
    submission = Submission.query.get_or_404(submission_id)
    if submission.user_id != current_user.id:
        flash('You do not have permission to view this submission.')
        return redirect(url_for('dashboard'))
    return render_template('result.html', submission=submission)

@app.route('/view_submissions')
@login_required
def view_submissions():
    submissions = Submission.query.filter_by(user_id=current_user.id).order_by(Submission.created_at.desc()).all()
    return render_template('view_submissions.html', submissions=submissions)

@app.route('/delete_submission/<int:submission_id>', methods=['POST'])
@login_required
def delete_submission(submission_id):
    submission = Submission.query.get_or_404(submission_id)
    if submission.user_id != current_user.id:
        return jsonify({'success': False, 'message': 'You do not have permission to delete this submission.'}), 403

    db.session.delete(submission)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/delete_resume/<int:resume_id>', methods=['POST'])
@login_required
def delete_resume(resume_id):
    resume = Resume.query.get_or_404(resume_id)
    if resume.user_id != current_user.id:
        return jsonify({'success': False, 'message': 'You do not have permission to delete this resume.'}), 403

    db.session.delete(resume)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/download_cover_letter/<int:submission_id>')
@login_required
def download_cover_letter(submission_id):
    submission = Submission.query.get_or_404(submission_id)
    if submission.user_id != current_user.id:
        flash('You do not have permission to download this cover letter.')
        return redirect(url_for('dashboard'))

    document = Document()
    document.add_paragraph(submission.cover_letter)

    doc_io = io.BytesIO()
    document.save(doc_io)
    doc_io.seek(0)

    return send_file(
        doc_io,
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        as_attachment=True,
        download_name=f"cover_letter_{submission.company_name}_{submission.job_title}.docx")

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        current_user.first_name = request.form.get('first_name')
        current_user.last_name = request.form.get('last_name')
        current_user.email = request.form.get('email')
        current_user.ai_model = request.form.get('ai_model')
        
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        if current_password and new_password and confirm_password:
            if current_user.check_password(current_password):
                if new_password == confirm_password:
                    current_user.set_password(new_password)
                    flash('Password updated successfully', 'success')
                else:
                    flash('New passwords do not match', 'error')
            else:
                flash('Current password is incorrect', 'error')
        
        db.session.commit()
        flash('Settings updated successfully', 'success')
        return redirect(url_for('settings'))
    
    return render_template('settings.html')

@app.route('/delete_account', methods=['POST'])
@login_required
def delete_account():
    current_user.delete_account()
    logout_user()
    flash('Your account has been deleted', 'success')
    return redirect(url_for('index'))

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')
        user = User.query.filter_by(email=email).first()
        if user:
            user.generate_reset_token()
            reset_link = url_for('reset_password', token=user.reset_token, _external=True)
            msg = Message('Password Reset Request',
                          sender=app.config['MAIL_DEFAULT_SENDER'],
                          recipients=[user.email])
            msg.body = f'To reset your password, visit the following link: {reset_link}'
            mail.send(msg)
            flash('An email has been sent with instructions to reset your password.', 'info')
        else:
            flash('Email address not found.', 'error')
        return redirect(url_for('login'))
    return render_template('forgot_password.html')

@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    user = User.query.filter_by(reset_token=token).first()
    if not user or not user.verify_reset_token(token):
        flash('Invalid or expired reset token.', 'error')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        if new_password == confirm_password:
            user.set_password(new_password)
            user.reset_token = None
            user.reset_token_expiration = None
            db.session.commit()
            flash('Your password has been reset successfully.', 'success')
            return redirect(url_for('login'))
        else:
            flash('Passwords do not match.', 'error')
    
    return render_template('reset_password.html', token=token)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        try:
            with db.engine.connect() as conn:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS first_name VARCHAR(80)'))
                conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS last_name VARCHAR(80)'))
                conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS ai_model VARCHAR(20) DEFAULT \'gpt-3.5-turbo\''))
                conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS reset_token VARCHAR(100)'))
                conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS reset_token_expiration TIMESTAMP'))
                conn.execute(text('ALTER TABLE submission ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP'))
                conn.execute(text('ALTER TABLE submission ADD COLUMN IF NOT EXISTS company_name VARCHAR(255)'))
                conn.execute(text('ALTER TABLE submission ADD COLUMN IF NOT EXISTS job_title VARCHAR(255)'))
                conn.commit()
        except Exception as e:
            logger.error(f'Error updating database schema: {e}')
    app.run(host='0.0.0.0', port=5000, debug=True)