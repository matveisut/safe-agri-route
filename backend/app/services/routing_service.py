import math
import networkx as nx
from typing import List, Tuple, Dict
from shapely.geometry import Polygon, Point, LineString
from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp

from app.models.field import Field
from app.models.risk_zone import RiskZone
from app.models.drone import Drone
from app.schemas.mission import RoutePoint, DroneRoute

# Ortools requires integer distance matrices.
# We multiply degrees distance by this scaler to get adequate precision integers
DISTANCE_SCALER = 100_000_000 

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
        
        # Grid loop
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
    def calculate_distance(p1: Point, p2: Point) -> float:
        """Euclidean distance computation."""
        return math.sqrt((p1.x - p2.x)**2 + (p1.y - p2.y)**2)

    @staticmethod
    def kmeans_clustering(points: List[Point], k: int, iterations: int = 15) -> List[List[Point]]:
        """Splits field points into k geographically distinct clusters."""
        if k <= 1 or not points:
            return [points]
            
        import random
        random.seed(42) # predictable sectors
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
    def build_graph(points: List[Point], risk_zones: List[RiskZone]) -> nx.Graph:
        """
        Builds a fully connected graph representing movement cost between any two points.
        If a line intersects a risk zone, weight penalty is applied.
        """
        G = nx.Graph()
        
        for i, p in enumerate(points):
            G.add_node(i, point=p)

        for i in range(len(points)):
            for j in range(i + 1, len(points)):
                p1 = points[i]
                p2 = points[j]
                
                base_dist = RoutingService.calculate_distance(p1, p2)
                
                # Check for risk zone intersection
                line = LineString([p1, p2])
                penalty_multiplier = 1.0
                
                for rz in risk_zones:
                    import shapely.wkb
                    rz_polygon = shapely.wkb.loads(bytes(rz.geometry.data))
                    
                    if rz_polygon.intersects(line):
                        # Extreme penalty to force path around the zone
                        penalty_multiplier += rz.severity_weight * 500 
                
                final_weight = base_dist * penalty_multiplier
                G.add_edge(i, j, weight=final_weight)
                
        return G

    @staticmethod
    def solve_cvrp(G: nx.Graph, points: List[Point], drones: List[Drone]) -> List[DroneRoute]:
        """
        Uses OR-Tools to solve CVRP given the graph connectivity and drone constraints.
        Note: Point 0 acts as a generic depot (start point for ease of routing).
        """
        num_nodes = len(points)
        num_vehicles = len(drones)
        depot = 0 # Assume 0th point is the start (depot)

        if num_nodes < 2 or num_vehicles == 0:
            return []

        # 1. Create distance/cost matrix
        distance_matrix = []
        for i in range(num_nodes):
            row = []
            for j in range(num_nodes):
                if i == j:
                    row.append(0)
                else:
                    # Retrieve weight, scale to int
                    w = G[i][j]['weight']
                    row.append(int(w * DISTANCE_SCALER))
            distance_matrix.append(row)

        # 2. Demands (we can assume visiting a node costs 1 unit or we can base it on distance. 
        # For CVRP, demand is usually what is delivered. Let's make visiting any point cost a tiny bit of battery)
        # However, to be realistic, battery is depleted across the *edges* (distance).
        # We model this by using Distance as the capacity dimension!
        capacities = []
        for d in drones:
            # Simplistic mapping: capacity proxy scaled by distance scaler
            # Let's assume drone capacity 5000 means it can cover X distance.
            # We will use battery capacity directly to limit path distance.
            max_dist_scaled = int((d.battery_capacity / 100) * DISTANCE_SCALER) 
            capacities.append(max_dist_scaled)

        # 3. Setup OR-Tools routing
        manager = pywrapcp.RoutingIndexManager(num_nodes, num_vehicles, depot)
        routing = pywrapcp.RoutingModel(manager)

        def distance_callback(from_index, to_index):
            from_node = manager.IndexToNode(from_index)
            to_node = manager.IndexToNode(to_index)
            return distance_matrix[from_node][to_node]

        transit_callback_index = routing.RegisterTransitCallback(distance_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

        # Add Distance constraint (as Battery constraint)
        dimension_name = 'Distance'
        routing.AddDimensionWithVehicleCapacity(
            transit_callback_index,
            0,  # null capacity slack
            capacities,  # vehicle maximum capacities
            True,  # start cumul to zero
            dimension_name)

        # Allow dropping visits if capacity is extremely strict (add penalty)
        penalty = 1000000
        for node in range(1, num_nodes):
            routing.AddDisjunction([manager.NodeToIndex(node)], penalty)

        # Setup parameters
        search_parameters = pywrapcp.DefaultRoutingSearchParameters()
        search_parameters.first_solution_strategy = (
            routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC)
        # Limit solver time to not block API long
        search_parameters.time_limit.seconds = 2

        # 4. Solve
        solution = routing.SolveWithParameters(search_parameters)

        routes = []
        if solution:
            for vehicle_id in range(num_vehicles):
                index = routing.Start(vehicle_id)
                route_pts = []
                while not routing.IsEnd(index):
                    node_index = manager.IndexToNode(index)
                    p = points[node_index]
                    route_pts.append(RoutePoint(lat=p.y, lng=p.x)) # Using x for lng, y for lat
                    index = solution.Value(routing.NextVar(index))
                
                # Append depot at the end (or drop it depending on business logic)
                node_index = manager.IndexToNode(index)
                p = points[node_index]
                route_pts.append(RoutePoint(lat=p.y, lng=p.x))
                
                # Assign to specific drone_id
                matched_drone = drones[vehicle_id]
                routes.append(DroneRoute(drone_id=matched_drone.id, route=route_pts))

        return routes

    @staticmethod
    def plan_mission(field: Field, drones: List[Drone], risk_zones: List[RiskZone], step_deg: float = 0.0002) -> List[DroneRoute]:
        """Main orchestrator for planning with K-Means clustering prior to VRP."""
        import shapely.wkb
        field_polygon = shapely.wkb.loads(bytes(field.geometry.data))
        
        # 1. Grid (safely excluding risk zones)
        points = RoutingService.generate_grid_for_polygon(field_polygon, risk_zones, step_deg)
        
        if not points or not drones:
            return []

        # 2. Cluster field into chunks matching drone count
        sorted_drones = sorted(drones, key=lambda d: d.battery_capacity, reverse=True)
        clusters = RoutingService.kmeans_clustering(points, len(sorted_drones), iterations=15)
        
        # Match largest cluster to highest capacity drone
        clusters.sort(key=len, reverse=True)
        
        all_routes = []
        for i, cluster_points in enumerate(clusters):
            if i >= len(sorted_drones):
                break
                
            drone = sorted_drones[i]
            
            # 3. Create independent Graph mapped only for this drone's cluster
            G = RoutingService.build_graph(cluster_points, risk_zones)
            
            # 4. Solve TSP / VRP locally
            routes = RoutingService.solve_cvrp(G, cluster_points, [drone])
            if routes:
                all_routes.extend(routes)
        
        return all_routes
