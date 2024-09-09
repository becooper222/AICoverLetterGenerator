from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from werkzeug.utils import secure_filename
import os
from utils.pdf_processor import extract_text_from_pdf

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['UPLOAD_FOLDER'] = '/tmp'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

ALLOWED_EXTENSIONS = {'pdf'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        # Check if the post request has the file part
        if 'resume' not in request.files:
            return redirect(request.url)
        file = request.files['resume']
        
        # If no file is selected, browser also submits an empty part without filename
        if file.filename == '':
            return redirect(request.url)
        
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            # Extract text from PDF
            resume_text = extract_text_from_pdf(filepath)
            
            # Store form data in session
            session['resume_text'] = resume_text
            session['focus_areas'] = request.form.get('focus_areas')
            session['job_description'] = request.form.get('job_description')
            
            # Remove the temporary file
            os.remove(filepath)
            
            return redirect(url_for('result'))
    
    return render_template('index.html')

@app.route('/result')
def result():
    resume_text = session.get('resume_text', '')
    focus_areas = session.get('focus_areas', '')
    job_description = session.get('job_description', '')
    
    combined_output = f"""
    Resume Content:
    {resume_text}

    Focus Areas:
    {focus_areas}

    Job Description:
    {job_description}
    """
    
    return render_template('result.html', combined_output=combined_output)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
