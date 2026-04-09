import axios from 'axios';

// Create base connection
const api = axios.create({
  baseURL: 'http://localhost:8000/api/v1',
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
    return Promise.reject(error);
  }
);

export default api;
