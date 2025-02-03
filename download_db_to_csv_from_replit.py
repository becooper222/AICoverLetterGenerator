import os
import psycopg2
import csv

# Load database credentials from environment variables
DB_NAME = os.getenv("PGDATABASE")
DB_USER = os.getenv("PGUSER")
DB_PASSWORD = os.getenv("PGPASSWORD")
DB_HOST = os.getenv("PGHOST")
DB_PORT = os.getenv("PGPORT")

# Establish connection
conn = psycopg2.connect(
    dbname=DB_NAME,
    user=DB_USER,
    password=DB_PASSWORD,
    host=DB_HOST,
    port=DB_PORT
)

cursor = conn.cursor()

# Get all table names in the public schema
cursor.execute("""
    SELECT tablename FROM pg_tables WHERE schemaname = 'public';
""")
tables = cursor.fetchall()

# Export each table to a CSV file
for table in tables:
    table_name = table[0]
    output_file = f"{table_name}.csv"

    # Fetch table data
    cursor.execute(f'SELECT * FROM "{table_name}"')
    rows = cursor.fetchall()
    colnames = [desc[0] for desc in cursor.description]

    # Write to CSV
    with open(output_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(colnames)  # Column headers
        writer.writerows(rows)  # Data rows
    
    print(f"Exported {table_name} to {output_file}")

# Close connection
cursor.close()
conn.close()


# pg_dump -U neondb_owner -h ep-bold-unit-a5zr94m7.us-east-2.aws.neon.tech -p 5432 -d neondb -F p -f database_backup.sql

