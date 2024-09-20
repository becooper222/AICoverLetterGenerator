from datetime import date

COVERLETTER_FORMAT = f"""
[Your Name]
[Your Address]
[City, State ZIP Code]
[Your Email]
[Your Phone Number]

{date.today().strftime("%B %d, %Y")}

[Hiring Manager's Name]
[Company Name]
[Company Address]
[City, State ZIP Code]

Dear [Hiring Manager's Name],

[Opening paragraph: Express your interest in the position and company. Mention how you learned about the job.]

[Body paragraph 1: Highlight your relevant skills and experiences that match the job requirements.]

[Body paragraph 2: Provide specific examples of your achievements and how they relate to the position.]

[Closing paragraph: Reiterate your interest, thank the reader for their time, and express your enthusiasm for an interview.]

Sincerely,

[Your Name]
"""
