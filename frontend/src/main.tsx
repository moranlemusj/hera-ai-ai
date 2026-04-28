import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { BrowserRouter, Route, Routes } from "react-router-dom"
import "./index.css"
import { Layout } from "@/components/Layout"
import { RunPage } from "@/pages/RunPage"
import { SettingsPage } from "@/pages/SettingsPage"

const root = document.getElementById("root")
if (!root) throw new Error("No #root element in index.html")

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<RunPage />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)
