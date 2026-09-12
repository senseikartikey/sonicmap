import asyncio

from app.services.catalog_jobs import worker_loop


if __name__ == "__main__":
    asyncio.run(worker_loop())
