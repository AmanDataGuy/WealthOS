"""
Alert consumer service — Kafka consumer for real-time portfolio alerts.
Status: Not yet implemented. Placeholder for Phase 3.

The consumer will subscribe to the 'portfolio-alerts' topic and trigger
WhatsApp/email notifications via composio_client.py when a holding crosses
a configured price threshold or a risk score changes materially.

Required env vars (when implemented):
    KAFKA_BOOTSTRAP_SERVERS=localhost:9092
    KAFKA_GROUP_ID=wealthos-alerts
"""

# TODO: Implement Kafka consumer using aiokafka
# Suggested implementation:
#
#   from aiokafka import AIOKafkaConsumer
#   from services.composio_client import send_notification
#
#   async def start_alert_consumer():
#       consumer = AIOKafkaConsumer(
#           "portfolio-alerts",
#           bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
#           group_id=os.getenv("KAFKA_GROUP_ID", "wealthos-alerts"),
#       )
#       await consumer.start()
#       async for msg in consumer:
#           alert = json.loads(msg.value)
#           await send_notification(alert["user_id"], alert["message"])
