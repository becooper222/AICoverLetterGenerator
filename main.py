from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, send_file
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import os
from utils.pdf_processor import extract_text_from_pdf
from openai import OpenAI
from sqlalchemy import text
from datetime import datetime, timedelta
import logging
import io
from docx import Document
from templates.cover_letter_format import COVERLETTER_FORMAT

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
    submissions = db.relationship('Submission', backref='user', lazy='dynamic')
    resumes = db.relationship('Resume', backref='user', lazy='dynamic')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Submission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    resume_text = db.Column(db.Text, nullable=False)
    focus_areas = db.Column(db.Text, nullable=False)
    job_description = db.Column(db.Text, nullable=False)
    cover_letter = db.Column(db.Text, nullable=False)
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

def generate_cover_letter_suggestion(resume_text, focus_areas, job_description, first_name, last_name):
    # Load the API key (already set in the environment)
    client = OpenAI(
        api_key=os.getenv('OPENAI_API_KEY')
    )

    # Prepare the full prompt
    static_prompt = "You are a professional cover letter writer."

    full_prompt = (
        f"{static_prompt}\n\n"
        f"Candidate Name: {first_name} {last_name}\n\n"
        f"Job Description: {job_description}\n\n"
        f"Cover Letter Format: {COVERLETTER_FORMAT}\n\n"
        f"Focus: {focus_areas}\n\n"
        f"My Resume:\n{resume_text}\n\n"
        f"Things to avoid in the writing:\n"
        "Do not use the phrase 'as advertised'. Do not use the word 'tenure'\n\n"
        "Please generate a cover letter that highlights my fit for this role, includes my name, and matches the format described in Cover Letter Format section."
    )

    # Call the ChatGPT API
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are a helpful cover letter writing assistant."},
            {"role": "user", "content": full_prompt}
        ]
    )

    # Extract and return the response text
    cover_letter = response.choices[0].message.content
    return cover_letter

@app.before_request
def before_request():
    logger.info(f"Before request: {request.endpoint}")
    logger.info(f"Current user: {current_user}")
    logger.info(f"Session: {session}")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        first_name = request.form.get('first_name')
        last_name = request.form.get('last_name')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        if user:
            flash('Username already exists')
            return render_template('register.html')
        
        user = User.query.filter_by(email=email).first()
        if user:
            flash('Email already exists')
            return render_template('register.html')
        
        new_user = User(username=username, email=email, first_name=first_name, last_name=last_name)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        
        flash('Registration successful. Please log in.')
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    logger.info("Entering login function")
    if current_user.is_authenticated:
        logger.info(f"User {current_user.username} is already authenticated")
        return redirect(url_for('submit'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        logger.info(f"Login attempt for username: {username}")
        
        user = User.query.filter_by(username=username).first()
        logger.info(f"User object: {user}")
        
        if user and user.check_password(password):
            login_user(user)
            logger.info(f"User {username} logged in successfully")
            logger.info(f"Current user after login: {current_user}")
            logger.info(f"Is user authenticated: {current_user.is_authenticated}")
            logger.info(f"Session after login: {session}")
            
            next_page = request.args.get('next')
            logger.info(f"Redirecting to: {next_page or url_for('submit')}")
            return redirect(next_page or url_for('submit'))
        else:
            logger.info(f"Invalid login attempt for username: {username}")
            flash('Invalid username or password')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/dashboard')
def dashboard():
    logger.info(f"Accessing dashboard - Current user: {current_user}")
    logger.info(f"Is authenticated: {current_user.is_authenticated}")
    logger.info(f"Session in dashboard: {session}")
    
    if current_user.is_authenticated:
        submissions = current_user.submissions.all()
        return render_template('dashboard.html', user=current_user, submissions=submissions)
    else:
        # Create dummy submissions for non-authenticated users
        dummy_submissions = [
            {'id': 1, 'focus_areas': 'Python, Flask', 'created_at': datetime.now()},
            {'id': 2, 'focus_areas': 'JavaScript, React', 'created_at': datetime.now() - timedelta(days=1)},
        ]
        return render_template('dashboard.html', submissions=dummy_submissions)

@app.route('/view_submissions')
@login_required
def view_submissions():
    submissions = Submission.query.filter_by(user_id=current_user.id).order_by(Submission.created_at.desc()).all()
    return render_template('view_submissions.html', submissions=submissions)

@app.route('/submit', methods=['GET', 'POST'])
@login_required
def submit():
    logger.info("Entering submit function")

    if request.method == 'POST':
        logger.info("Processing POST request for submission")
        resume_selection = request.form.get('resume_selection')
        
        if resume_selection and resume_selection != 'new':
            # Use selected saved resume
            resume = Resume.query.get(resume_selection)
            if resume and resume.user_id == current_user.id:
                resume_text = resume.content
                filename = resume.filename
            else:
                flash('Invalid resume selection')
                return redirect(request.url)
        else:
            # Process new file upload
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
                
                # Save the new resume
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
        cover_letter = generate_cover_letter_suggestion(resume_text, focus_areas, job_description, current_user.first_name, current_user.last_name)

        new_submission = Submission(
            resume_text=resume_text,
            focus_areas=focus_areas,
            job_description=job_description,
            cover_letter=cover_letter,
            user_id=current_user.id
        )
        db.session.add(new_submission)
        db.session.commit()
        logger.info(f"New submission created: {new_submission.id}")

        return redirect(url_for('result', submission_id=new_submission.id))

    # GET request
    saved_resumes = Resume.query.filter_by(user_id=current_user.id).order_by(Resume.created_at.desc()).all()
    return render_template('submit.html', saved_resumes=saved_resumes)


@app.route('/result/<int:submission_id>')
@login_required
def result(submission_id):
    submission = Submission.query.get_or_404(submission_id)
    if submission.user_id != current_user.id:
        logger.warning(f"User {current_user.id} attempted to access submission {submission_id} without permission")
        flash('You do not have permission to view this submission')
        return redirect(url_for('view_submissions'))
    
    return render_template('result.html', submission=submission)

@app.route('/download_cover_letter/<int:submission_id>')
@login_required
def download_cover_letter(submission_id):
    submission = Submission.query.get_or_404(submission_id)
    if submission.user_id != current_user.id:
        logger.warning(f"User {current_user.id} attempted to download cover letter for submission {submission_id} without permission")
        flash('You do not have permission to download this cover letter')
        return redirect(url_for('view_submissions'))
    
    cover_letter_content = submission.cover_letter
    doc = Document()
    doc.add_paragraph(cover_letter_content)
    
    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f'cover_letter_{submission_id}.docx',
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    )

@app.route('/delete_submission/<int:submission_id>', methods=['POST'])
@login_required
def delete_submission(submission_id):
    submission = Submission.query.get_or_404(submission_id)
    if submission.user_id != current_user.id:
        flash('You do not have permission to delete this submission')
        return redirect(url_for('view_submissions'))
    
    db.session.delete(submission)
    db.session.commit()
    flash('Submission deleted successfully')
    return redirect(url_for('view_submissions'))

@app.route('/delete_resume/<int:resume_id>', methods=['POST'])
@login_required
def delete_resume(resume_id):
    resume = Resume.query.get_or_404(resume_id)
    if resume.user_id != current_user.id:
        flash('You do not have permission to delete this resume')
        return redirect(url_for('submit'))
    
    db.session.delete(resume)
    db.session.commit()
    flash('Resume deleted successfully')
    return redirect(url_for('submit'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        try:
            with db.engine.connect() as conn:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS first_name VARCHAR(80)'))
                conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS last_name VARCHAR(80)'))
                conn.execute(text('ALTER TABLE submission ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP'))
                conn.commit()
        except Exception as e:
            logger.error(f'Error updating database schema: {e}')
    app.run(host='0.0.0.0', port=5000, debug=True)
