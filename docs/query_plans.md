# Query plans (measured)

Scale: {"vehicles": 100000, "alerts": 1500000, "risk_rows": 100000, "vectors": 50000, "postgres": "16.2 embedded (pgserver), 1 vCPU sandbox"}

## Q1

**Before: 131.859 ms**
```
Limit  (cost=26838.43..26841.35 rows=25 width=34) (actual time=135.565..136.543 rows=25 loops=1)
  Buffers: shared hit=14303 read=649 written=60
  ->  Gather Merge  (cost=26838.43..27143.66 rows=2616 width=34) (actual time=135.564..136.539 rows=25 loops=1)
        Workers Planned: 2
        Workers Launched: 2
        Buffers: shared hit=14303 read=649 written=60
        ->  Sort  (cost=25838.41..25841.68 rows=1308 width=34) (actual time=128.123..128.126 rows=20 loops=3)
              Sort Key: raised_at DESC, id DESC
              Sort Method: top-N heapsort  Memory: 28kB
              Buffers: shared hit=14303 read=649 written=60
              Worker 0:  Sort Method: top-N heapsort  Memory: 28kB
              Worker 1:  Sort Method: top-N heapsort  Memory: 27kB
              ->  Parallel Seq Scan on alert  (cost=0.00..25801.50 rows=1308 width=34) (actual time=0.110..127.761 rows=980 loops=3)
                    Filter: ((severity >= 4) AND (tenant_id = 3) AND (status = 'OPEN'::text))
```

**After: 0.020 ms**
```
Limit  (cost=0.41..91.93 rows=25 width=34) (actual time=0.005..0.012 rows=25 loops=1)
  Buffers: shared hit=28
  ->  Index Scan using alert_open_critical_idx on alert  (cost=0.41..11201.55 rows=3060 width=34) (actual time=0.005..0.010 rows=25 loops=1)
        Index Cond: (tenant_id = 3)
        Buffers: shared hit=28
Planning Time: 0.032 ms
Execution Time: 0.017 ms
```

## Q2

**Before: 25.401 ms**
```
Limit  (cost=4966.83..4966.90 rows=25 width=26) (actual time=24.552..24.557 rows=25 loops=1)
  Buffers: shared hit=1816
  ->  Sort  (cost=4966.83..4975.33 rows=3397 width=26) (actual time=24.551..24.554 rows=25 loops=1)
        Sort Key: r.score DESC, r.vehicle_id DESC
        Sort Method: top-N heapsort  Memory: 28kB
        Buffers: shared hit=1816
        ->  Hash Join  (cost=2632.46..4870.97 rows=3397 width=26) (actual time=5.606..23.888 rows=3458 loops=1)
              Hash Cond: (v.id = r.vehicle_id)
              Buffers: shared hit=1816
              ->  Seq Scan on vehicle v  (cost=0.00..1976.00 rows=100000 width=22) (actual time=0.003..8.385 rows=100000 loops=1)
                    Buffers: shared hit=976
              ->  Hash  (cost=2590.00..2590.00 rows=3397 width=8) (actual time=5.596..5.597 rows=3458 loops=1)
                    Buckets: 4096  Batches: 1  Memory Usage: 168kB
                    Buffers: shared hit=840
```

**After: 0.081 ms**
```
Limit  (cost=0.71..71.07 rows=25 width=26) (actual time=0.011..0.049 rows=25 loops=1)
  Buffers: shared hit=103
  ->  Nested Loop  (cost=0.71..9538.93 rows=3389 width=26) (actual time=0.011..0.047 rows=25 loops=1)
        Buffers: shared hit=103
        ->  Index Only Scan using vehicle_risk_rank_idx on vehicle_risk r  (cost=0.42..3480.34 rows=3389 width=8) (actual time=0.008..0.015 rows=25 loops=1)
              Index Cond: ((tenant_id = 3) AND (ROW(score, vehicle_id) < ROW('0.08'::double precision, 60000)))
              Heap Fetches: 25
              Buffers: shared hit=28
        ->  Index Scan using vehicle_pkey on vehicle v  (cost=0.29..1.79 rows=1 width=22) (actual time=0.001..0.001 rows=1 loops=25)
              Index Cond: (id = r.vehicle_id)
              Buffers: shared hit=75
Planning:
  Buffers: shared hit=24
Planning Time: 0.162 ms
```

## Q3

**Before: 202.749 ms**
```
Finalize GroupAggregate  (cost=26832.74..27341.47 rows=1835 width=24) (actual time=198.221..199.411 rows=20 loops=1)
  Group Key: v.fleet_id, a.type_code
  Buffers: shared hit=17412 read=490 written=11
  ->  Gather Merge  (cost=26832.74..27295.60 rows=3670 width=24) (actual time=198.205..199.397 rows=60 loops=1)
        Workers Planned: 2
        Workers Launched: 2
        Buffers: shared hit=17412 read=490 written=11
        ->  Partial GroupAggregate  (cost=25832.71..25871.96 rows=1835 width=24) (actual time=191.279..191.490 rows=20 loops=3)
              Group Key: v.fleet_id, a.type_code
              Buffers: shared hit=17412 read=490 written=11
              ->  Sort  (cost=25832.71..25837.94 rows=2090 width=16) (actual time=191.261..191.335 rows=1688 loops=3)
                    Sort Key: v.fleet_id, a.type_code
                    Sort Method: quicksort  Memory: 117kB
                    Buffers: shared hit=17412 read=490 written=11
```

**After: 0.692 ms**
```
HashAggregate  (cost=1183.72..1189.81 rows=487 width=48) (actual time=0.612..0.617 rows=20 loops=1)
  Group Key: fleet_id, type_code
  Batches: 1  Memory Usage: 49kB
  Buffers: shared hit=487
  ->  Bitmap Heap Scan on mv_fleet_alert_daily  (cost=66.74..1179.54 rows=557 width=24) (actual time=0.228..0.496 rows=568 loops=1)
        Recheck Cond: ((fleet_id = ANY ('{3,23,43,63}'::integer[])) AND (day >= '2026-09-02'::date))
        Heap Blocks: exact=465
        Buffers: shared hit=487
        ->  Bitmap Index Scan on mv_fleet_alert_daily_fleet_id_type_code_day_idx  (cost=0.00..66.60 rows=557 width=0) (actual time=0.183..0.184 rows=568 loops=1)
              Index Cond: ((fleet_id = ANY ('{3,23,43,63}'::integer[])) AND (day >= '2026-09-02'::date))
              Buffers: shared hit=22
Planning:
  Buffers: shared hit=1
Planning Time: 0.067 ms
```

## Q5

**Before: 15.274 ms**
```
Limit  (cost=2170.48..2170.49 rows=5 width=16) (actual time=14.196..14.197 rows=5 loops=1)
  Buffers: shared hit=715
  ->  Sort  (cost=2170.48..2295.48 rows=50000 width=16) (actual time=14.195..14.195 rows=5 loops=1)
        Sort Key: ((embedding <=> '[0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5]'::vector))
        Sort Method: top-N heapsort  Memory: 25kB
        Buffers: shared hit=715
        ->  Seq Scan on fault_case  (cost=0.00..1340.00 rows=50000 width=16) (actual time=0.005..9.274 rows=50000 loops=1)
              Buffers: shared hit=715
Planning Time: 0.034 ms
Execution Time: 14.206 ms
```

**After: 0.077 ms**
```
Limit  (cost=16.60..16.95 rows=5 width=16) (actual time=0.059..0.061 rows=5 loops=1)
  Buffers: shared hit=142
  ->  Index Scan using fault_case_hnsw on fault_case  (cost=16.60..3501.60 rows=50000 width=16) (actual time=0.059..0.061 rows=5 loops=1)
        Order By: (embedding <=> '[0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5]'::vector)
        Buffers: shared hit=142
Planning:
  Buffers: shared hit=1
Planning Time: 0.012 ms
Execution Time: 0.066 ms
```

## Deep pagination
OFFSET 1500: **0.797 ms**
```
Limit  (cost=5491.17..5582.68 rows=25 width=34) (actual time=0.558..0.569 rows=25 loops=1)
  Buffers: shared hit=1535
  ->  Index Scan using alert_open_critical_idx on alert  (cost=0.41..11201.55 rows=3060 width=34) (actual time=0.005..0.514 rows=1525 loops=1)
        Index Cond: (tenant_id = 3)
        Buffers: shared hit=1535
Planning Time: 0.039 ms
Execution Time: 0.576 ms
```
Keyset: **0.021 ms**
```
Limit  (cost=0.41..96.59 rows=25 width=34) (actual time=0.005..0.013 rows=25 loops=1)
  Buffers: shared hit=28
  ->  Index Scan using alert_open_critical_idx on alert  (cost=0.41..5705.72 rows=1483 width=34) (actual time=0.005..0.011 rows=25 loops=1)
        Index Cond: ((tenant_id = 3) AND (ROW(raised_at, id) < ROW('2026-08-16 10:44:06+00'::timestamp with time zone, 1094073)))
        Buffers: shared hit=28
Planning Time: 0.039 ms
Execution Time: 0.020 ms
```
