# First, specify the base Docker image.
FROM apify/actor-python:3.12

# Copy requirements first for better layer caching.
COPY --chown=myuser:myuser requirements.txt ./

# Install Python dependencies.
RUN echo "Python version:" \
 && python --version \
 && echo "Installing dependencies:" \
 && pip install --no-cache-dir -r requirements.txt

# Copy the Actor source code.
COPY --chown=myuser:myuser . ./

# Verify the code compiles.
RUN python -m compileall -q sam_gov/

# Run the Actor.
CMD ["python", "-m", "sam_gov"]
