import os
import time
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, send_file
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from utils.pdf_processor import extract_text_from_pdf
from openai import OpenAI
from datetime import datetime, timedelta, date
import logging
import io
from docx import Document
from docx.shared import Pt
from docx.enum.style import WD_STYLE_TYPE
import secrets
from flask_mail import Mail, Message
from supabase import create_client, Client
from typing import Optional

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['UPLOAD_FOLDER'] = '/tmp'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# Initialize Supabase client
supabase: Client = create_client(
    os.getenv('SUPABASE_URL'),
    os.getenv('SUPABASE_SERVICE_ROLE_KEY')
)

# Email configuration
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_USERNAME')
mail = Mail(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

ALLOWED_EXTENSIONS = {'pdf'}

COVERLETTER_FORMAT = (
    "On the first line (line 1) include the candidate name and nothing else. On the next line (line 2), "
    "put [current date]. Skip a line (line 3). On the next line (line 4), "
    "put \"Dear Hiring Manager,\", or if the name of the hiring manager's name is present in the job description, put \"Dear "
    "[Hiring Manager Name],\". Skip a line (line 5). Start the body of the cover letter. Write three, four, or five paragraphs, "
    "with empty lines in between each, that combine to reach the bottom of a word doc minus 3 lines. Skip a line (line end-3). On the next "
    "line put, \"Sincerely,\" (line end-2). On the final line (line end-1), put the candidate name"
)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_db_connection():
    for attempt in range(max_retries):
        try:
            connection = db.engine.connect()
            # Test the connection with a simple query
            connection.execute(text('SELECT 1'))
            return connection
        except OperationalError as e:
            if attempt < max_retries - 1:
                logger.warning(f"Database connection attempt {attempt + 1} failed. Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay * (attempt + 1))  # Exponential backoff
            else:
                logger.error(f"Failed to connect to Supabase PostgreSQL after {max_retries} attempts.")
                logger.error(f"Error details: {str(e)}")
                raise
        except SQLAlchemyError as e:
            logger.error(f"Unexpected database error: {str(e)}")
            raise

class User(UserMixin):
    def __init__(self, user_data):
        self.id = user_data.get('id')
        self.username = user_data.get('username')
        self.email = user_data.get('email')
        self.first_name = user_data.get('first_name')
        self.last_name = user_data.get('last_name')
        self.password_hash = user_data.get('password_hash')
        self.ai_model = user_data.get('ai_model', 'gpt-4o-2024-08-06')
        self.reset_token = user_data.get('reset_token')
        self.reset_token_expiration = user_data.get('reset_token_expiration')
        self.cover_letter_format = user_data.get('cover_letter_format', COVERLETTER_FORMAT)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def delete_account(self):
        try:
            # Delete all submissions
            supabase.table('submission').delete().eq('user_id', self.id).execute()
            # Delete all resumes
            supabase.table('resume').delete().eq('user_id', self.id).execute()
            # Delete user
            supabase.table('user').delete().eq('id', self.id).execute()
            return True
        except Exception as e:
            logger.error(f"Error deleting account: {str(e)}")
            return False

    def generate_reset_token(self):
        self.reset_token = secrets.token_urlsafe(32)
        self.reset_token_expiration = datetime.utcnow() + timedelta(hours=1)
        try:
            supabase.table('user').update({
                'reset_token': self.reset_token,
                'reset_token_expiration': self.reset_token_expiration.isoformat()
            }).eq('id', self.id).execute()
            return True
        except Exception as e:
            logger.error(f"Error generating reset token: {str(e)}")
            return False

    def verify_reset_token(self, token):
        if token != self.reset_token:
            return False
        expiration = datetime.fromisoformat(self.reset_token_expiration) if isinstance(self.reset_token_expiration, str) else self.reset_token_expiration
        if expiration < datetime.utcnow():
            return False
        return True

class Submission:
    def __init__(self, submission_data):
        self.id = submission_data.get('id')
        self.resume_text = submission_data.get('resume_text')
        self.focus_areas = submission_data.get('focus_areas')
        self.job_description = submission_data.get('job_description')
        self.cover_letter = submission_data.get('cover_letter')
        self.company_name = submission_data.get('company_name')
        self.job_title = submission_data.get('job_title')
        self.user_id = submission_data.get('user_id')
        self.created_at = datetime.fromisoformat(submission_data.get('created_at')) if submission_data.get('created_at') else datetime.utcnow()

    @staticmethod
    def get_by_id(submission_id):
        try:
            response = supabase.table('submission').select('*').eq('id', submission_id).execute()
            if response.data and len(response.data) > 0:
                return Submission(response.data[0])
            return None
        except Exception as e:
            logger.error(f"Error getting submission: {str(e)}")
            return None

class Resume:
    def __init__(self, resume_data):
        self.id = resume_data.get('id')
        self.filename = resume_data.get('filename')
        self.content = resume_data.get('content')
        self.user_id = resume_data.get('user_id')
        self.created_at = datetime.fromisoformat(resume_data.get('created_at')) if resume_data.get('created_at') else datetime.utcnow()

    @staticmethod
    def get_by_id(resume_id):
        try:
            response = supabase.table('resume').select('*').eq('id', resume_id).execute()
            if response.data and len(response.data) > 0:
                return Resume(response.data[0])
            return None
        except Exception as e:
            logger.error(f"Error getting resume: {str(e)}")
            return None

@login_manager.user_loader
def load_user(user_id):
    try:
        response = supabase.table('user').select('*').eq('id', user_id).execute()
        if response.data and len(response.data) > 0:
            return User(response.data[0])
        return None
    except Exception as e:
        logger.error(f"Error loading user: {str(e)}")
        return None

def extract_company_and_job_title(job_description):
    try:
        # Initialize OpenAI client with just the API key
        client = OpenAI()  # It will automatically use OPENAI_API_KEY from environment

        prompt = f"""
        Extract the company name and job title from the following job description:
        
        {job_description}
        
        Return the information in the following format:
        Company: [Company Name]
        Job Title: [Job Title]
        """

        response = client.chat.completions.create(
            model="gpt-4o-mini",  # Using a stable model for extraction
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant that extracts company names and job titles from job descriptions."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.3,
            max_tokens=100
        )

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
    except Exception as e:
        logger.error(f"Error extracting company and job title: {str(e)}")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Error details: {str(e)}")
        raise

def generate_cover_letter_suggestion(resume_text, focus_areas, job_description, first_name, last_name, ai_model, cover_letter_format):
    try:
        logger.info("Starting cover letter generation process")
        # Override ai_model parameter to use default model

        #########################################################
        ai_model = "gpt-4o-2024-11-20" # Look here when you want to change the model back to using the user specified model
        #########################################################

        logger.info(f"Using AI model: {ai_model}")
        
        # Initialize OpenAI client with API key from environment
        try:
            api_key = os.getenv('OPENAI_API_KEY')
            if not api_key:
                logger.error("OPENAI_API_KEY environment variable is not set")
                raise ValueError("OpenAI API key is not configured")

            client = OpenAI(api_key=api_key)
            logger.info("Successfully initialized OpenAI client")
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI client: {str(e)}")
            raise

        logger.info("Extracting company and job title")
        company_name, job_title = extract_company_and_job_title(job_description)
        logger.info(f"Extracted - Company: {company_name}, Job Title: {job_title}")

        current_date = date.today().strftime("%B %d, %Y")
        logger.info(f"Using current date: {current_date}")

        static_prompt = "You are a professional cover letter writer."

        full_prompt = (
            f"{static_prompt}\n\n"
            f"Candidate Name: {first_name} {last_name}\n\n"
            f"Current Date: {current_date}\n\n"
            f"Company: {company_name}\n"
            f"Job Title: {job_title}\n\n"
            f"Job Description: {job_description}\n\n"
            f"Cover Letter Format: {cover_letter_format}\n\n"
            f"Focus: {focus_areas}\n\n"
            f"My Resume:\n{resume_text}\n\n"
            f"Things to avoid in the writing:\n"
            "Do not use the phrase 'as advertised'. Do not use the word 'tenure'\n\n"
            f"Please generate a cover letter that highlights my fit for this role, includes my name, the current date ({current_date}), and matches the format described in Cover Letter Format section."
        )
        
        logger.info("Sending request to OpenAI API")
        try:
            response = client.chat.completions.create(
                model=ai_model,  # Using the user's selected model
                messages=[
                    {"role": "system", "content": "You are a helpful cover letter writing assistant."},
                    {"role": "user", "content": full_prompt}
                ],
                temperature=0.7,
                max_tokens=2000
            )
            logger.info("Successfully received response from OpenAI API")
        except Exception as e:
            logger.error(f"OpenAI API request failed: {str(e)}")
            raise

        cover_letter = response.choices[0].message.content
        logger.info("Successfully generated cover letter")
        
        return cover_letter, company_name, job_title
    except Exception as e:
        logger.error(f"Error generating cover letter: {str(e)}")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Error details: {str(e)}")
        raise

def handle_db_error(e):
    logger.error(f"Database error: {str(e)}")
    if isinstance(e, OperationalError):
        logger.error("This might be an SSL connection error. Please check your database configuration and SSL settings.")
    db.session.rollback()
    flash("An error occurred while accessing the database. Please try again later.", "error")

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

        try:
            # Check if username already exists
            response = supabase.table('user').select('id').eq('username', username).execute()
            if response.data and len(response.data) > 0:
                flash('Username already exists')
                return redirect(url_for('register'))

            # Create new user
            password_hash = generate_password_hash(password)
            new_user_data = {
                'username': username,
                'email': email,
                'first_name': first_name,
                'last_name': last_name,
                'password_hash': password_hash,
                'cover_letter_format': COVERLETTER_FORMAT,
                'ai_model': 'gpt-4o-2024-08-06'
            }
            
            response = supabase.table('user').insert(new_user_data).execute()
            if response.data and len(response.data) > 0:
                logger.info(f"New user registered: {username}")
                return redirect(url_for('login'))
            else:
                flash('Error creating user')
                return redirect(url_for('register'))
        except Exception as e:
            logger.error(f"Error during registration: {str(e)}")
            flash('An error occurred during registration')
            return redirect(url_for('register'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        try:
            response = supabase.table('user').select('*').eq('username', username).execute()
            if response.data and len(response.data) > 0:
                user_data = response.data[0]
                user = User(user_data)
                if user and user.check_password(password):
                    login_user(user)
                    return redirect(url_for('dashboard'))
            flash('Invalid username or password')
        except Exception as e:
            logger.error(f"Error during login: {str(e)}")
            flash('An error occurred during login')
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

        try:
            if resume_selection and resume_selection != 'new':
                resume = Resume.get_by_id(resume_selection)
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

                    new_resume_data = {
                        'filename': filename,
                        'content': resume_text,
                        'user_id': current_user.id,
                        'created_at': datetime.utcnow().isoformat()
                    }
                    response = supabase.table('resume').insert(new_resume_data).execute()
                    if not response.data:
                        raise Exception("Failed to save resume")

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
                resume_text, focus_areas, job_description, current_user.first_name,
                current_user.last_name, current_user.ai_model,
                current_user.cover_letter_format)

            logger.info(f"Extracted company name: {company_name}")
            logger.info(f"Extracted job title: {job_title}")

            new_submission_data = {
                'resume_text': resume_text,
                'focus_areas': focus_areas,
                'job_description': job_description,
                'cover_letter': cover_letter,
                'company_name': company_name,
                'job_title': job_title,
                'user_id': current_user.id,
                'created_at': datetime.utcnow().isoformat()
            }
            response = supabase.table('submission').insert(new_submission_data).execute()
            if not response.data:
                raise Exception("Failed to save submission")
            
            submission_id = response.data[0]['id']
            logger.info(f"New submission created: {submission_id}")

            return redirect(url_for('result', submission_id=submission_id))
        except Exception as e:
            logger.error(f"Error during submission: {str(e)}")
            flash('An error occurred during submission')
            return redirect(request.url)

    response = supabase.table('resume').select('*').eq('user_id', current_user.id).order('created_at', desc=True).execute()
    saved_resumes = [Resume(resume_data) for resume_data in response.data] if response.data else []
    return render_template('submit.html', saved_resumes=saved_resumes)

@app.route('/result/<int:submission_id>')
@login_required
def result(submission_id):
    submission = Submission.get_by_id(submission_id)
    if submission.user_id != current_user.id:
        flash('You do not have permission to view this submission.')
        return redirect(url_for('dashboard'))
    return render_template('result.html', submission=submission)

@app.route('/view_submissions')
@login_required
def view_submissions():
    try:
        response = supabase.table('submission').select('*').eq('user_id', current_user.id).order('created_at', desc=True).execute()
        submissions = [Submission(submission_data) for submission_data in response.data] if response.data else []
        return render_template('view_submissions.html', submissions=submissions)
    except Exception as e:
        logger.error(f"Error viewing submissions: {str(e)}")
        flash('An error occurred while loading submissions')
        return redirect(url_for('dashboard'))

@app.route('/delete_submission/<int:submission_id>', methods=['POST'])
@login_required
def delete_submission(submission_id):
    try:
        response = supabase.table('submission').select('user_id').eq('id', submission_id).execute()
        if not response.data or len(response.data) == 0 or response.data[0]['user_id'] != current_user.id:
            return jsonify({'success': False, 'message': 'You do not have permission to delete this submission.'}), 403

        response = supabase.table('submission').delete().eq('id', submission_id).execute()
        if response.data:
            return jsonify({'success': True})
        return jsonify({'success': False, 'message': 'Failed to delete submission.'}), 500
    except Exception as e:
        logger.error(f"Error deleting submission: {str(e)}")
        return jsonify({'success': False, 'message': 'An error occurred while deleting the submission.'}), 500

@app.route('/delete_resume/<int:resume_id>', methods=['POST'])
@login_required
def delete_resume(resume_id):
    try:
        response = supabase.table('resume').select('user_id').eq('id', resume_id).execute()
        if not response.data or len(response.data) == 0 or response.data[0]['user_id'] != current_user.id:
            return jsonify({'success': False, 'message': 'You do not have permission to delete this resume.'}), 403

        response = supabase.table('resume').delete().eq('id', resume_id).execute()
        if response.data:
            return jsonify({'success': True})
        return jsonify({'success': False, 'message': 'Failed to delete resume.'}), 500
    except Exception as e:
        logger.error(f"Error deleting resume: {str(e)}")
        return jsonify({'success': False, 'message': 'An error occurred while deleting the resume.'}), 500

@app.route('/download_cover_letter/<int:submission_id>')
@login_required
def download_cover_letter(submission_id):
    submission = Submission.get_by_id(submission_id)
    if not submission or submission.user_id != current_user.id:
        flash('You do not have permission to download this cover letter.')
        return redirect(url_for('dashboard'))

    document = Document()
    
    style = document.styles['Normal']
    font = style.font
    font.name = 'Times New Roman'
    font.size = Pt(12)

    document.add_paragraph(submission.cover_letter)

    doc_io = io.BytesIO()
    document.save(doc_io)
    doc_io.seek(0)

    filename = f"{submission.company_name} - {submission.job_title} Cover Letter.docx"
    filename = "".join(c for c in filename if c.isalnum() or c in (' ', '-', '_', '.'))  # Sanitize filename

    return send_file(
        doc_io,
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        as_attachment=True,
        download_name=filename)

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        try:
            update_data = {
                'first_name': request.form.get('first_name'),
                'last_name': request.form.get('last_name'),
                'email': request.form.get('email'),
                'ai_model': request.form.get('ai_model'),
                'cover_letter_format': request.form.get('cover_letter_format')
            }

            current_password = request.form.get('current_password')
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')

            if current_password and new_password and confirm_password:
                if current_user.check_password(current_password):
                    if new_password == confirm_password:
                        update_data['password_hash'] = generate_password_hash(new_password)
                        flash('Password updated successfully', 'success')
                    else:
                        flash('New passwords do not match', 'error')
                else:
                    flash('Current password is incorrect', 'error')

            response = supabase.table('user').update(update_data).eq('id', current_user.id).execute()
            if response.data:
                # Update the current user object with new data
                for key, value in update_data.items():
                    setattr(current_user, key, value)
                flash('Settings updated successfully', 'success')
            else:
                flash('Failed to update settings', 'error')
        except Exception as e:
            logger.error(f"Error updating settings: {str(e)}")
            flash('An error occurred while updating settings', 'error')
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
        try:
            response = supabase.table('user').select('*').eq('email', email).execute()
            if response.data and len(response.data) > 0:
                user = User(response.data[0])
                if user.generate_reset_token():
                    reset_link = url_for('reset_password', token=user.reset_token, _external=True)
                    msg = Message('Password Reset Request', sender=app.config['MAIL_DEFAULT_SENDER'], recipients=[user.email])
                    msg.body = f'To reset your password, visit the following link: {reset_link}'
                    mail.send(msg)
                    flash('An email has been sent with instructions to reset your password.', 'info')
                else:
                    flash('An error occurred while generating reset token.', 'error')
            else:
                flash('Email address not found.', 'error')
        except Exception as e:
            logger.error(f"Error in forgot password: {str(e)}")
            flash('An error occurred while processing your request.', 'error')
        return redirect(url_for('login'))
    return render_template('forgot_password.html')

@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    try:
        response = supabase.table('user').select('*').eq('reset_token', token).execute()
        if not response.data or len(response.data) == 0:
            flash('Invalid or expired reset token.', 'error')
            return redirect(url_for('login'))

        user = User(response.data[0])
        if not user.verify_reset_token(token):
            flash('Invalid or expired reset token.', 'error')
            return redirect(url_for('login'))

        if request.method == 'POST':
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')
            if new_password == confirm_password:
                update_data = {
                    'password_hash': generate_password_hash(new_password),
                    'reset_token': None,
                    'reset_token_expiration': None
                }
                response = supabase.table('user').update(update_data).eq('id', user.id).execute()
                if response.data:
                    flash('Your password has been reset successfully.', 'success')
                    return redirect(url_for('login'))
                else:
                    flash('Failed to reset password.', 'error')
            else:
                flash('Passwords do not match.', 'error')
    except Exception as e:
        logger.error(f"Error in reset password: {str(e)}")
        flash('An error occurred while resetting your password.', 'error')
        return redirect(url_for('login'))

    return render_template('reset_password.html', token=token)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)