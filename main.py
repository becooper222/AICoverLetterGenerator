from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from werkzeug.utils import secure_filename
import os
from utils.pdf_processor import extract_text_from_pdf
from openai import OpenAI

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['UPLOAD_FOLDER'] = '/tmp'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

ALLOWED_EXTENSIONS = {'pdf'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def generate_cover_letter_suggestion(resume_text, focus_areas, job_description):
    # Load the API key (already set in the environment)
    client = OpenAI(
        api_key=os.getenv('OPENAI_API_KEY')
    )

    # Prepare the full prompt
    static_prompt = "You are a professional cover letter writer."
    coverletter_format = "Please write a professional cover letter that is tailored to the job description and highlights the candidate's relevant skills and experience."

    full_prompt = (
        f"{static_prompt}\n\n"
        f"Job Description: {job_description}\n\n"
        f"Cover Letter Format: {coverletter_format}\n\n"
        f"Focus: {focus_areas}\n\n"
        f"My Resume:\n{resume_text}\n\n"
        f"Things to avoid in the writing:\n"
        "Do not use the phrase 'as advertised'. Do not use the word 'tenure'\n\n"
        "Please generate a cover letter that highlights my fit for this role and matches the format described in Cover Letter Format section."
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
    
    cover_letter_suggestion = generate_cover_letter_suggestion(resume_text, focus_areas, job_description)
    
    combined_output = f"""
    Resume Content:
    {resume_text}

    Focus Areas:
    {focus_areas}

    Job Description:
    {job_description}
    """
    
    return render_template('result.html', combined_output=combined_output, cover_letter_suggestion=cover_letter_suggestion)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
