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
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from templates.cover_letter_format import COVERLETTER_FORMAT

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

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

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

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
    
    # Set up the document
    sections = doc.sections
    for section in sections:
        section.top_margin = Inches(1)
        section.bottom_margin = Inches(1)
        section.left_margin = Inches(1)
        section.right_margin = Inches(1)
    
    # Add current date
    date = doc.add_paragraph()
    date_run = date.add_run(datetime.now().strftime("%B %d, %Y"))
    date_run.font.size = Pt(11)
    date.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    
    # Add user's name
    name = doc.add_paragraph()
    name_run = name.add_run(f"{current_user.first_name} {current_user.last_name}")
    name_run.bold = True
    name_run.font.size = Pt(14)
    name.alignment = WD_ALIGN_PARAGRAPH.LEFT
    
    # Add a line break
    doc.add_paragraph()
    
    # Add cover letter content
    for paragraph in cover_letter_content.split('\n\n'):
        p = doc.add_paragraph()
        p_run = p.add_run(paragraph)
        p_run.font.size = Pt(11)
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p.paragraph_format.space_after = Pt(12)
        p.paragraph_format.first_line_indent = Inches(0.5)
    
    # Add signature
    doc.add_paragraph()
    signature = doc.add_paragraph("Sincerely,")
    signature.alignment = WD_ALIGN_PARAGRAPH.LEFT
    doc.add_paragraph()
    name_signature = doc.add_paragraph(f"{current_user.first_name} {current_user.last_name}")
    name_signature.alignment = WD_ALIGN_PARAGRAPH.LEFT
    
    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f'cover_letter_{submission_id}.docx',
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    )

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000, debug=True)
