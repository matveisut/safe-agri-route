/** Базовый URL API (REST и WebSocket без префикса /api/v1). */

const host =
  typeof window !== 'undefined' ? window.location.hostname : 'localhost';

export const API_ORIGIN = import.meta.env.VITE_API_ORIGIN ?? `http://${host}:8000`;
export const WS_ORIGIN = import.meta.env.VITE_WS_ORIGIN ?? `ws://${host}:8000`;

