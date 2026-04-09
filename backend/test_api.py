import json
import urllib.request
import urllib.error
import traceback

def test_routing():
    url = "http://localhost:8000/api/v1/mission/plan"
    
    payload = {
        "field_id": 1,
        "drone_ids": [1, 2, 3]
    }
    
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST')
    req.add_header('Content-Type', 'application/json')
    
    print(f"Sending POST request to {url}...")
    
    try:
        with urllib.request.urlopen(req, timeout=30.0) as response:
            if response.status == 200:
                response_data = json.loads(response.read().decode('utf-8'))
                print("\n✅ Success! Routes generated.")
                
                routes = response_data.get("routes", [])
                print(f"Total drones used: {len(routes)}")
                
                for r in routes:
                    drone_id = r.get("drone_id")
                    route_pts = r.get("route", [])
                    print(f"  -> Drone {drone_id} covers {len(route_pts)} points.")
            else:
                print(f"\n❌ Failed with status code: {response.status}")
                
    except urllib.error.HTTPError as e:
        print(f"\n❌ HTTP Session Error: {e.code} - {e.reason}")
        print(e.read().decode('utf-8'))
    except Exception as e:
        print(f"\n❌ System Error: {e.__class__.__name__}: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    test_routing()
