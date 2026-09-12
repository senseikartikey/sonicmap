import logging

from app.services.stem_jobs import worker_loop


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    worker_loop()
