web: gunicorn config.wsgi:application
worker: python manage.py run_scheduler --username mprov --watchlist Default --max-symbols 25 --throttle-seconds 2
