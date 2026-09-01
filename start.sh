#!/bin/bash
python -c "import app; app.iniciar_banco()"
exec gunicorn --bind 0.0.0.0:$PORT app:app
