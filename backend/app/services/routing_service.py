import math
import networkx as nx
from dataclasses import dataclass
from typing import List, Tuple
from shapely.geometry import Polygon, Point, LineString
from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp

from app.models.field import Field
from app.models.risk_zone import RiskZone
from app.models.drone import Drone
from app.schemas.mission import RoutePoint, DroneRoute
from app.services.risk_map import build_risk_map, get_risk_for_point

# Ortools requires integer distance matrices.
# We multiply degrees distance by this scaler to get adequate precision integers
DISTANCE_SCALER = 100_000_000


@dataclass
class MissionPlanResult:
    routes: List[DroneRoute]
    reliability_index: float
    estimated_coverage_pct: float


class RoutingService:
    @staticmethod
    def generate_grid_for_polygon(polygon: Polygon, risk_zones: List[RiskZone], step: float = 0.0002) -> List[Point]:
        """
        Creates a grid of points covering the given Shapely polygon bounding box,
        keeping only points that intersect the polygon. Excludes points falling inside risk zones.
        """
        minx, miny, maxx, maxy = polygon.bounds
        points = []

        import shapely.wkb
        rz_polygons = [shapely.wkb.loads(bytes(rz.geometry.data)) for rz in risk_zones]

        x = minx
        while x <= maxx:
            y = miny
            while y <= maxy:
                p = Point(x, y)
                if polygon.contains(p):
                    in_risk = False
                    for rz_poly in rz_polygons:
                        if rz_poly.contains(p):
                            in_risk = True
                            break
                    if not in_risk:
                        points.append(p)
                y += step
            x += step

        return points

    @staticmethod
    def generate_weighted_grid(
        polygon: Polygon,
        risk_zones: List[RiskZone],
        step: float = 0.0002,
    ) -> Tuple[List[Tuple[Point, float]], int]:
        """
        Generates grid points with risk-based weights. Excludes points inside risk zones.

        Returns:
            (weighted_safe_points, total_field_points_count)
            where weighted_safe_points is a list of (Point, weight) tuples,
            weight = 1.0 / (1.0 - risk + 1e-6), risk ∈ [0, 1].
        """
        import shapely.wkb

        minx, miny, maxx, maxy = polygon.bounds
        rz_data: List[Tuple[Polygon, float]] = [
            (shapely.wkb.loads(bytes(rz.geometry.data)), rz.severity_weight)
            for rz in risk_zones
        ]
        # Proximity radius within which a zone influences risk value of a safe point
        influence_radius = 5.0 * step

        total_count = 0
        weighted_points: List[Tuple[Point, float]] = []

        x = minx
        while x <= maxx:
            y = miny
            while y <= maxy:
                p = Point(x, y)
                if polygon.contains(p):
                    total_count += 1
                    in_risk = any(rz_poly.contains(p) for rz_poly, _ in rz_data)
                    if not in_risk:
                        risk = RoutingService._proximity_risk(p, rz_data, influence_radius)
                        weight = 1.0 / (1.0 - risk + 1e-6)
                        weighted_points.append((p, weight))
                y += step
            x += step

        return weighted_points, total_count

    @staticmethod
    def _proximity_risk(
        point: Point,
        rz_data: List[Tuple[Polygon, float]],
        influence_radius: float,
    ) -> float:
        """
        Compute risk ∈ [0, 1] for a safe point based on proximity to risk zone boundaries.
        Risk decreases linearly from severity_weight at distance 0 to 0 at influence_radius.
        """
        max_risk = 0.0
        for rz_poly, severity in rz_data:
            dist = rz_poly.exterior.distance(point)
            if dist < influence_radius:
                risk = severity * (1.0 - dist / influence_radius)
                if risk > max_risk:
                    max_risk = risk
        return min(max_risk, 1.0)

    @staticmethod
    def calculate_distance(p1: Point, p2: Point) -> float:
        """Euclidean distance computation."""
        return math.sqrt((p1.x - p2.x) ** 2 + (p1.y - p2.y) ** 2)

    @staticmethod
    def kmeans_clustering(points: List[Point], k: int, iterations: int = 15) -> List[List[Point]]:
        """Splits field points into k geographically distinct clusters (legacy, kept for reference)."""
        if k <= 1 or not points:
            return [points]

        import random
        random.seed(42)
        centroids = random.sample(points, min(k, len(points)))
        clusters = [[] for _ in range(k)]

        for _ in range(iterations):
            clusters = [[] for _ in range(k)]
            for p in points:
                min_dist = float('inf')
                best_cls = 0
                for idx, c in enumerate(centroids):
                    dist = RoutingService.calculate_distance(p, c)
                    if dist < min_dist:
                        min_dist = dist
                        best_cls = idx
                clusters[best_cls].append(p)

            for i in range(k):
                if clusters[i]:
                    avg_x = sum(p.x for p in clusters[i]) / len(clusters[i])
                    avg_y = sum(p.y for p in clusters[i]) / len(clusters[i])
                    centroids[i] = Point(avg_x, avg_y)

        return [c for c in clusters if c]

    @staticmethod
    def risk_weighted_voronoi(
        weighted_points: List[Tuple[Point, float]],
        k: int,
        max_iterations: int = 50,
    ) -> List[List[Tuple[Point, float]]]:
        """
        Weighted Lloyd's algorithm: partitions field points into k zones.

        Centroid update rule:
            new_center = sum(point * weight) / sum(weight)

        Converges when max centroid shift < 1e-6 or max_iterations reached.
        """
        if not weighted_points:
            return []
        if k <= 1:
            return [weighted_points]

        n = len(weighted_points)
        k = min(k, n)

        # Uniform initialisation: pick evenly-spaced points as starting centroids
        step = max(1, n // k)
        centroids = [weighted_points[i * step][0] for i in range(k)]

        zones: List[List[Tuple[Point, float]]] = [[] for _ in range(k)]

        for _ in range(max_iterations):
            new_zones: List[List[Tuple[Point, float]]] = [[] for _ in range(k)]

            # Assignment step
            for p, w in weighted_points:
                best = 0
                min_dist = float('inf')
                for idx, c in enumerate(centroids):
                    d = RoutingService.calculate_distance(p, c)
                    if d < min_dist:
                        min_dist = d
                        best = idx
                new_zones[best].append((p, w))

            # Update step: move each centroid to the weighted mean of its zone
            max_shift = 0.0
            new_centroids: List[Point] = []
            for i in range(k):
                if new_zones[i]:
                    total_w = sum(w for _, w in new_zones[i])
                    cx = sum(p.x * w for p, w in new_zones[i]) / total_w
                    cy = sum(p.y * w for p, w in new_zones[i]) / total_w
                    new_c = Point(cx, cy)
                    shift = RoutingService.calculate_distance(centroids[i], new_c)
                    if shift > max_shift:
                        max_shift = shift
                    new_centroids.append(new_c)
                else:
                    # Keep old centroid if zone is empty
                    new_centroids.append(centroids[i])

            centroids = new_centroids
            zones = new_zones

            if max_shift < 1e-6:
                break

        return [z for z in zones if z]

    @staticmethod
    def build_graph(points: List[Point], risk_zones: List[RiskZone]) -> nx.Graph:
        """
        Builds a fully connected graph representing movement cost between any two points.
        If a line intersects a risk zone, weight penalty is applied.
        """
        import shapely.wkb

        G = nx.Graph()
        for i, p in enumerate(points):
            G.add_node(i, point=p)

        rz_cache = [
            (shapely.wkb.loads(bytes(rz.geometry.data)), rz.severity_weight)
            for rz in risk_zones
        ]

        for i in range(len(points)):
            for j in range(i + 1, len(points)):
                p1 = points[i]
                p2 = points[j]

                base_dist = RoutingService.calculate_distance(p1, p2)
                line = LineString([p1, p2])
                penalty_multiplier = 1.0

                for rz_polygon, severity in rz_cache:
                    if rz_polygon.intersects(line):
                        # Extreme penalty to force path around the zone
                        penalty_multiplier += severity * 500

                G.add_edge(i, j, weight=base_dist * penalty_multiplier)

        return G

    @staticmethod
    def solve_cvrp(G: nx.Graph, points: List[Point], drones: List[Drone]) -> List[DroneRoute]:
        """
        Uses OR-Tools to solve CVRP given the graph connectivity and drone constraints.
        Note: Point 0 acts as a generic depot (start point for ease of routing).
        """
        num_nodes = len(points)
        num_vehicles = len(drones)
        depot = 0

        if num_nodes < 2 or num_vehicles == 0:
            return []

        # Distance/cost matrix
        distance_matrix = []
        for i in range(num_nodes):
            row = []
            for j in range(num_nodes):
                if i == j:
                    row.append(0)
                else:
                    w = G[i][j]['weight']
                    row.append(int(w * DISTANCE_SCALER))
            distance_matrix.append(row)

        # Battery-based capacity (distance proxy)
        capacities = [
            int((d.battery_capacity / 100) * DISTANCE_SCALER)
            for d in drones
        ]

        manager = pywrapcp.RoutingIndexManager(num_nodes, num_vehicles, depot)
        routing = pywrapcp.RoutingModel(manager)

        def distance_callback(from_index, to_index):
            return distance_matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]

        transit_callback_index = routing.RegisterTransitCallback(distance_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

        routing.AddDimensionWithVehicleCapacity(
            transit_callback_index,
            0,
            capacities,
            True,
            'Distance',
        )

        penalty = 1_000_000
        for node in range(1, num_nodes):
            routing.AddDisjunction([manager.NodeToIndex(node)], penalty)

        search_parameters = pywrapcp.DefaultRoutingSearchParameters()
        search_parameters.first_solution_strategy = (
            routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC)
        search_parameters.time_limit.seconds = 2

        solution = routing.SolveWithParameters(search_parameters)

        routes = []
        if solution:
            for vehicle_id in range(num_vehicles):
                index = routing.Start(vehicle_id)
                route_pts = []
                while not routing.IsEnd(index):
                    node_index = manager.IndexToNode(index)
                    p = points[node_index]
                    route_pts.append(RoutePoint(lat=p.y, lng=p.x))
                    index = solution.Value(routing.NextVar(index))
                # Append final depot point
                node_index = manager.IndexToNode(index)
                p = points[node_index]
                route_pts.append(RoutePoint(lat=p.y, lng=p.x))
                routes.append(DroneRoute(drone_id=drones[vehicle_id].id, route=route_pts))

        return routes

    @staticmethod
    def plan_mission(
        field: Field,
        drones: List[Drone],
        risk_zones: List[RiskZone],
        step_deg: float = 0.0002,
    ) -> MissionPlanResult:
        """
        Main orchestrator: build_risk_map → Risk-Weighted Voronoi → greedy drone assignment → TSP.

        Pipeline:
          1. Convert RiskZone ORM models to plain dicts and call build_risk_map().
          2. Filter safe (non-zone) grid points; weight each by its risk value.
          3. Partition safe points via risk_weighted_voronoi() (Lloyd's algorithm).
          4. Assign drones greedily: heaviest zone → strongest drone.
          5. Solve TSP per zone via OR-Tools (PATH_CHEAPEST_ARC) with REB penalty edges.
          6. Compute IRM via get_risk_for_point() on every waypoint.
          7. Return MissionPlanResult with routes, reliability_index, estimated_coverage_pct.
        """
        import shapely.wkb

        field_polygon = shapely.wkb.loads(bytes(field.geometry.data))

        # --- 1. Convert ORM models → dicts for build_risk_map ---
        zone_dicts = [
            {
                "geometry": shapely.wkb.loads(bytes(rz.geometry.data)),
                "severity": rz.severity_weight,
                "zone_type": rz.type,
            }
            for rz in risk_zones
        ]

        # --- 2. Build risk grid (all field points, including inside risk zones) ---
        risk_grid, grid_points_latlon, grid_indices = build_risk_map(
            field_polygon, zone_dicts, grid_step=step_deg
        )

        # grid_meta enables get_risk_for_point() lookups later
        minx, miny, _, _ = field_polygon.bounds
        grid_meta = {"minx": minx, "miny": miny, "step": step_deg}

        total_field_points = len(grid_points_latlon)  # includes cells inside risk zones

        # --- 3. Build weighted_points for Voronoi (safe points only) ---
        # Points directly inside any risk zone are excluded from drone routes.
        weighted_points: List[Tuple[Point, float]] = []
        for (lat, lng), (i, j) in zip(grid_points_latlon, grid_indices):
            p = Point(lng, lat)  # Shapely: x=lng, y=lat
            in_risk = any(z["geometry"].contains(p) for z in zone_dicts)
            if not in_risk:
                risk_val = float(risk_grid[i, j])
                weight = 1.0 / (1.0 - risk_val + 1e-6)
                weighted_points.append((p, weight))

        if not weighted_points or not drones:
            return MissionPlanResult(routes=[], reliability_index=1.0, estimated_coverage_pct=0.0)

        # --- 4. Risk-Weighted Voronoi partition ---
        k = len(drones)
        zones = RoutingService.risk_weighted_voronoi(weighted_points, k)

        # --- 5. Greedy drone → zone assignment ---
        #   zone_load  = sum of point weights  (higher → more hazardous / dense zone)
        #   drone rank = battery_capacity * max_speed  (higher → more capable drone)
        zone_loads = [sum(w for _, w in zone) for zone in zones]
        zone_order = sorted(range(len(zones)), key=lambda idx: zone_loads[idx], reverse=True)
        sorted_zones = [zones[idx] for idx in zone_order]

        sorted_drones = sorted(
            drones,
            key=lambda d: d.battery_capacity * d.max_speed,
            reverse=True,
        )

        # --- 6. Build TSP routes per drone + collect waypoint risks for IRM ---
        all_routes: List[DroneRoute] = []
        all_waypoint_risks: List[float] = []
        reachable_points = 0

        for i, zone in enumerate(sorted_zones):
            if i >= len(sorted_drones):
                break

            drone = sorted_drones[i]
            zone_points = [p for p, _ in zone]
            reachable_points += len(zone_points)

            # Graph with REB-penalty edges (unchanged from original)
            G = RoutingService.build_graph(zone_points, risk_zones)
            routes = RoutingService.solve_cvrp(G, zone_points, [drone])

            if routes:
                all_routes.extend(routes)
                for route in routes:
                    for rp in route.route:
                        # Use build_risk_map grid for IRM (consistent risk model)
                        risk = get_risk_for_point(risk_grid, grid_meta, rp.lat, rp.lng)
                        all_waypoint_risks.append(risk)

        # --- 7. IRM = 1 - mean(waypoint risks) ---
        if all_waypoint_risks:
            reliability_index = 1.0 - sum(all_waypoint_risks) / len(all_waypoint_risks)
        else:
            reliability_index = 1.0

        # --- 8. Coverage = reachable safe points / total field points ---
        estimated_coverage_pct = (
            (reachable_points / total_field_points * 100.0)
            if total_field_points > 0
            else 0.0
        )

        return MissionPlanResult(
            routes=all_routes,
            reliability_index=float(max(0.0, min(1.0, reliability_index))),
            estimated_coverage_pct=float(estimated_coverage_pct),
        )
