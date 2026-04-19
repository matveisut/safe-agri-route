import axios from 'axios';

import { API_ORIGIN } from '../config';

// Base URL совпадает с `config.ts` (hostname + опционально VITE_API_ORIGIN)
const api = axios.create({
  baseURL: `${API_ORIGIN}/api/v1`,
  timeout: 60000,
});

// Request Interceptor
api.interceptors.request.use(
  (config) => {
    // Usually tokens come from localStorage or Auth contexts
    const token = localStorage.getItem('access_token');
    
    // Add auth header if we have token
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// Response Interceptor for generic error catching
api.interceptors.response.use(
  (response) => response,
  (error) => {
    console.error("API Error occurred: ", error.response?.data || error.message);
    if (error.response?.status === 401) {
      localStorage.removeItem('access_token');
      window.dispatchEvent(new Event('auth:logout'));
    }
    return Promise.reject(error);
  }
);

export default api;
