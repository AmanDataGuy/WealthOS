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
from workflows.morning_briefing import (
    fetch_briefing_data,
    generate_briefing,
    send_briefing,
    MorningBriefingWorkflow,
)

BRIEFING_QUEUE = "wealthos-briefing-queue"

async def main():
    print("Connecting to Temporal server at localhost:7233...")
    client = await Client.connect("localhost:7233")
    print("Connected.")

    main_worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[WealthOSWorkflow],
        activities=[
            finance_activity, data_activity, research_activity,
            risk_activity, code_activity, rebalancing_activity,
            writer_activity, mem0_write_activity,
        ],
    )

    briefing_worker = Worker(
        client,
        task_queue=BRIEFING_QUEUE,
        workflows=[MorningBriefingWorkflow],
        activities=[fetch_briefing_data, generate_briefing, send_briefing],
    )

    print(f"Worker polling: {TASK_QUEUE} and {BRIEFING_QUEUE}")
    print("Press Ctrl+C to stop.\n")
    await asyncio.gather(main_worker.run(), briefing_worker.run())

if __name__ == "__main__":
    asyncio.run(main())
