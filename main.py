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
    client = OpenAI(
        api_key=os.getenv('OPENAI_API_KEY')
    )

    prompt = f"""
    Extract the company name and job title from the following job description:
    
    {job_description}
    
    Return the information in the following format:
    Company: [Company Name]
    Job Title: [Job Title]
    """

    response = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are a helpful assistant that extracts company names and job titles from job descriptions."},
            {"role": "user", "content": prompt}
        ]
    )

    extracted_info = response.choices[0].message.content
    company_name = ""
    job_title = ""

    for line in extracted_info.split('\n'):
        if line.startswith("Company:"):
            company_name = line.split("Company:")[1].strip()
        elif line.startswith("Job Title:"):
            job_title = line.split("Job Title:")[1].strip()

    return company_name, job_title

def generate_cover_letter_suggestion(resume_text, focus_areas, job_description, first_name, last_name):
    client = OpenAI(
        api_key=os.getenv('OPENAI_API_KEY')
    )

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
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are a helpful cover letter writing assistant."},
            {"role": "user", "content": full_prompt}
        ]
    )

    cover_letter = response.choices[0].message.content
    return cover_letter, company_name, job_title

# ... (keep the rest of the code unchanged)

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
        cover_letter, company_name, job_title = generate_cover_letter_suggestion(resume_text, focus_areas, job_description, current_user.first_name, current_user.last_name)

        new_submission = Submission(
            resume_text=resume_text,
            focus_areas=focus_areas,
            job_description=job_description,
            cover_letter=cover_letter,
            company_name=company_name,
            job_title=job_title,
            user_id=current_user.id
        )
        db.session.add(new_submission)
        db.session.commit()
        logger.info(f"New submission created: {new_submission.id}")

        return redirect(url_for('result', submission_id=new_submission.id))

    saved_resumes = Resume.query.filter_by(user_id=current_user.id).order_by(Resume.created_at.desc()).all()
    return render_template('submit.html', saved_resumes=saved_resumes)

# ... (keep the rest of the code unchanged)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        try:
            with db.engine.connect() as conn:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS first_name VARCHAR(80)'))
                conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS last_name VARCHAR(80)'))
                conn.execute(text('ALTER TABLE submission ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP'))
                conn.execute(text('ALTER TABLE submission ADD COLUMN IF NOT EXISTS company_name VARCHAR(255)'))
                conn.execute(text('ALTER TABLE submission ADD COLUMN IF NOT EXISTS job_title VARCHAR(255)'))
                conn.commit()
        except Exception as e:
            logger.error(f'Error updating database schema: {e}')
    app.run(host='0.0.0.0', port=5000, debug=True)
