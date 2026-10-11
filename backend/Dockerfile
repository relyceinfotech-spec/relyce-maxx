FROM python:3.13-slim
RUN pip install --no-cache-dir uv==0.12.7
WORKDIR /workspace/backend
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/app ./app
COPY supabase /workspace/supabase
RUN uv sync --frozen --no-dev
RUN useradd --create-home maxxx
USER maxxx
EXPOSE 8000
ENV HOST=0.0.0.0
CMD [".venv/bin/maxxx-api"]
