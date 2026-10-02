"""Trip / stop segmentation of noisy speed samples via 2-state dynamic programming (Viterbi).

Cost = sum of per-sample emission cost + switch_penalty per state change.
Emission cost: MOVING state penalises low speed, STOPPED state penalises high speed.
Complexity: O(n) time, O(n) space (back-pointers). A naive threshold flips on every GPS jitter.
"""
STOPPED, MOVING = 0, 1


def segment_states(speeds_kmh, switch_penalty: float = 40.0, v_move: float = 8.0):
    n = len(speeds_kmh)
    if n == 0:
        return []
    INF = float("inf")
    cost = [[0.0, 0.0] for _ in range(n)]
    back = [[0, 0] for _ in range(n)]

    def emit(state, v):
        # distance from the state's expected regime; capped so one spike cannot dominate
        return min(v, v_move) if state == STOPPED else min(max(v_move - v, 0.0), v_move)

    cost[0] = [emit(STOPPED, speeds_kmh[0]), emit(MOVING, speeds_kmh[0])]
    for i in range(1, n):
        for s in (STOPPED, MOVING):
            stay = cost[i - 1][s]
            switch = cost[i - 1][1 - s] + switch_penalty
            if stay <= switch:
                cost[i][s], back[i][s] = stay + emit(s, speeds_kmh[i]), s
            else:
                cost[i][s], back[i][s] = switch + emit(s, speeds_kmh[i]), 1 - s
    s = STOPPED if cost[-1][STOPPED] <= cost[-1][MOVING] else MOVING
    states = [0] * n
    for i in range(n - 1, -1, -1):
        states[i] = s
        s = back[i][s]
    return states


def segments(states):
    """Run-length encode -> [(state, start_idx, end_idx_inclusive)]."""
    out, start = [], 0
    for i in range(1, len(states) + 1):
        if i == len(states) or states[i] != states[start]:
            out.append((states[start], start, i - 1))
            start = i
    return out
