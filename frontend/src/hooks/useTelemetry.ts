import { useEffect, useRef, useState } from 'react';
import { useMissionStore, DroneRoute } from '../store/useMissionStore';

export function useTelemetry() {
  const wsRef = useRef<WebSocket | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  
  const { plannedRoutes, updateTelemetry, telemetry } = useMissionStore();

  useEffect(() => {
    // Cleanup on unmount
    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, []);

  const startSimulation = () => {
    if (!plannedRoutes || plannedRoutes.length === 0) {
      alert("No planned routes available. Please generate a route first.");
      return;
    }

    if (wsRef.current) {
      wsRef.current.close();
    }

    // Connect to WebSocket using native browser API
    // Ensure you use the right host. Assuming backend is on 8000
    const wsUrl = `ws://localhost:8000/ws/telemetry`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log("Telemetry WebSocket connected.");
      setIsConnected(true);
      
      // Send the current routes to start simulating
      ws.send(JSON.stringify({ routes: plannedRoutes }));
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        
        if (data.message) {
          console.log("Server message:", data.message);
          if (data.message === "Mission Completed") {
            setIsConnected(false);
          }
          return;
        }

        if (data.telemetry) {
          // data.telemetry is an array of { drone_id, lat, lng, status }
          data.telemetry.forEach((t: any) => {
            updateTelemetry(t.drone_id, { lat: t.lat, lng: t.lng });
          });
        }
      } catch (err) {
        console.error("Error parsing telemetry data:", err);
      }
    };

    ws.onclose = () => {
      console.log("Telemetry WebSocket closed.");
      setIsConnected(false);
    };

    ws.onerror = (err) => {
      console.error("Telemetry WebSocket error:", err);
      setIsConnected(false);
    };
  };

  const stopSimulation = () => {
    if (wsRef.current) {
      wsRef.current.close();
      setIsConnected(false);
    }
  };

  return {
    startSimulation,
    stopSimulation,
    isConnected
  };
}
