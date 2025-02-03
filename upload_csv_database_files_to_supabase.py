import pandas as pd
import psycopg2
from dotenv import load_dotenv
import os

# Load environment variables from .env
load_dotenv()

# Fetch database credentials
USER = os.getenv("user")
PASSWORD = os.getenv("password")
HOST = os.getenv("host")
PORT = os.getenv("port")
DBNAME = os.getenv("dbname")

# Path to your CSV files (Update these paths if needed)
csv_files = {
    "resume": "resume.csv",  # Update with actual path
    "submission": "submission.csv",  # Update with actual path
    "user": "user.csv"  # Update with actual path
}

# Connect to Supabase PostgreSQL
try:
    connection = psycopg2.connect(
        user=USER,
        password=PASSWORD,
        host=HOST,
        port=PORT,
        dbname=DBNAME
    )
    print("✅ Connection to Supabase successful!")

    cursor = connection.cursor()

    # Iterate through CSV files and upload data
    for table_name, file_path in csv_files.items():
        print(f"📤 Uploading {file_path} to {table_name}...")

        # Load the CSV file into a Pandas DataFrame
        df = pd.read_csv(file_path)

        # Dynamically generate the INSERT statement
        columns = ", ".join(df.columns)
        values_placeholder = ", ".join(["%s"] * len(df.columns))
        insert_query = f"INSERT INTO {table_name} ({columns}) VALUES ({values_placeholder})"

        # Insert each row
        for _, row in df.iterrows():
            cursor.execute(insert_query, tuple(row))

        connection.commit()
        print(f"✅ Successfully uploaded {file_path} to {table_name}!")

    # Close connections
    cursor.close()
    connection.close()
    print("🔌 Connection closed.")

except Exception as e:
    print(f"❌ Error: {e}")
