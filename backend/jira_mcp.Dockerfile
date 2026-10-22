FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp_servers/__init__.py ./mcp_servers/__init__.py
COPY mcp_servers/jira ./mcp_servers/jira
EXPOSE 8001
CMD ["python", "mcp_servers/jira/server.py"]
