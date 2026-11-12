import React from 'react'
import { createRoot } from 'react-dom/client'
import { AnimateUIProvider } from '@animate-ui/react/provider'
import { App } from './app.jsx'
import './styles.css'

const el = document.getElementById('dorado-data')
const initial = el ? JSON.parse(el.textContent) : { page: 'login' }

createRoot(document.getElementById('root')).render(<React.StrictMode><AnimateUIProvider tempo={0.96}><App initial={initial} /></AnimateUIProvider></React.StrictMode>)
