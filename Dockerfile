# Dockerfile

# 1. Use an official Python runtime as a parent image
# Using slim-buster for a smaller image size
FROM python:3.10-slim-buster

# 2. Set the working directory in the container
WORKDIR /app

# 3. Copy the requirements file into the container at /app
COPY requirements.txt .

# 4. Install any needed packages specified in requirements.txt
# --no-cache-dir reduces image size
# --trusted-host pypi.python.org handles potential network issues
RUN pip install --no-cache-dir --trusted-host pypi.python.org --trusted-host files.pythonhosted.org --trusted-host pypi.org -r requirements.txt

# 5. Copy the rest of your application code into the container at /app
COPY . .

# 6. Make port 8000 available to the world outside this container
# This is the port Gunicorn will listen on *inside* the container
EXPOSE 8000

# 7. Define environment variable defaults (optional, can be overridden)
# We'll set the actual secrets in Azure App Service configuration
# ENV FLASK_APP=app.py # Not strictly needed when using gunicorn entrypoint

# 8. Run app.py when the container launches using Gunicorn
# Binds Gunicorn to all interfaces (0.0.0.0) on port 8000 inside the container
# 'app:app' refers to the 'app' Flask object within your 'app.py' file
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "app:app"]