import { BrowserRouter, Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import ExposureTable from "./pages/ExposureTable";
import PersonDetail from "./pages/PersonDetail";
import ReviewQueue from "./pages/ReviewQueue";
import RunTraces from "./pages/RunTraces";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="exposure-table" element={<ExposureTable />} />
          <Route path="persons/:id" element={<PersonDetail />} />
          <Route path="review" element={<ReviewQueue />} />
          <Route path="runs" element={<RunTraces />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
