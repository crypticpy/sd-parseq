import './wdyr';

import React from 'react';
import ReactDOM from 'react-dom/client';
import {
  createBrowserRouter,
  RouterProvider,
} from "react-router-dom";
import './index.css';
import App from './App';
import Interpreter from './Interpreter';
import Deforum from './Deforum';
import reportWebVitals from './reportWebVitals';
import TimeAgo from 'javascript-time-ago'
import en from 'javascript-time-ago/locale/en.json'
import * as Sentry from "@sentry/react";
import { BrowserTracing } from "@sentry/tracing";
import { Routes, Route } from "react-router-dom";

Sentry.init({
  dsn: "https://4706cbba5987462184a3e541c4b8a9d4@o175750.ingest.sentry.io/4504274009325568",
  integrations: [new BrowserTracing()],
  environment: process.env.NODE_ENV,

  // Set tracesSampleRate to 1.0 to capture 100%
  // of transactions for performance monitoring.
  // We recommend adjusting this value in production
  tracesSampleRate: 1.0,
});

TimeAgo.addDefaultLocale(en)

const router = createBrowserRouter([
  {
    path: "/",
    element: <Deforum />,
  },
  {
    path: "/interpreter",
    element: <Interpreter />,
  },
  {
    path: "/deforum",
    element: <Deforum />,
  },
  {
    path: "/legacy",
    element: <App />,
  },
]);

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>
);

// If you want to start measuring performance in your app, pass a function
// to log results (for example: reportWebVitals(console.log))
// or send to an analytics endpoint. Learn more: https://bit.ly/CRA-vitals
reportWebVitals();
