# workflows/temporal_worker.py
"""
Temporal worker — run this as a separate process alongside the API.

  python -m workflows.temporal_worker

Registers all WealthOS activities and the WealthOSWorkflow,
then polls the task queue indefinitely.
"""

import asyncio
from temporalio.client import Client
from temporalio.worker import Worker

from workflows.temporal_workflows import (
    TASK_QUEUE,
    WealthOSWorkflow,
    finance_activity,
    data_activity,
    research_activity,
    risk_activity,
    code_activity,
    rebalancing_activity,
    writer_activity,
    mem0_write_activity,
)


async def main():
    print("Connecting to Temporal server at localhost:7233...")
    client = await Client.connect("localhost:7233")
    print("Connected.")

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[WealthOSWorkflow],
        activities=[
            finance_activity,
            data_activity,
            research_activity,
            risk_activity,
            code_activity,
            rebalancing_activity,
            writer_activity,
            mem0_write_activity,
        ],
    )

    print(f"Worker polling task queue: {TASK_QUEUE}")
    print("Press Ctrl+C to stop.\n")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())