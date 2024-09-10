from datetime import date

# Get the current date in format "Month Day, Year"
date = date.today().strftime("%B %d, %Y")

COVERLETTER_FORMAT = (
    "On the first line (line 1) include the candidate name and nothing else. On the next line (line 2), "
    + f"put {date}. Skip a line (line 3). On the next line (line 4), " +
    "put “Dear Hiring Manager,”, or if the name of the hiring manager’s name is present in the job description, put “Dear "
    +
    "[Hiring Manager Name],”. Skip a line (line 5). Start the body of the cover letter. Write three, four, or five paragraphs, "
    +
    "with empty lines in between each, that combine to reach the bottom of a word doc minus 3 lines. Skip a line (line -3). On the next "
    +
    f"line put, “Sincerely,” (line -2). On the final line (line -1), put the candidate name"
)
