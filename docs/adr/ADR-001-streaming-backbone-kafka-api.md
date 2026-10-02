# ADR-001: Kafka API as the streaming backbone (Redpanda locally, MSK/Strimzi in cloud)
**Status:** accepted
**Context:** 100k vehicles x 1 Hz = 100k events/s sustained, 3x bursts, need replay, per-vehicle ordering, and no cloud lock-in (the brief cites AWS IoT FleetWise closing to new customers).
**Decision:** Use the Kafka wire protocol. Topic `telemetry` has 48 partitions keyed by VIN, so one consumer sees all events of a vehicle in order and per-vehicle state needs no coordination. Locally we run Redpanda (single binary, Kafka-compatible); in cloud, MSK / Confluent / Strimzi. Application code is identical; only `KAFKA_BROKERS` changes.
**Consequences:** (+) durable log with replay and consumer-group scaling; (+) back-pressure via bounded producer queue and pause-before-poll consumers; (-) ordering is per partition only; (-) partition count caps consumer parallelism (48 -> up to 48 ingest pods), so re-partitioning needs planning. Alternatives rejected: RabbitMQ (no cheap replay of days of data), vendor IoT services (lock-in).

# Delivery semantics
At-least-once from the broker plus idempotent processing (dedupe on (vin, seq), ReplacingMergeTree sink) gives an exactly-once *effect* without transactional-producer overhead. Offsets are committed only after the batch is durably written.
