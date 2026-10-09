import {
  ChartNoAxesCombined,
  Coins,
  FileCode2,
  FlaskConical,
  LayoutDashboard,
  Network,
  Radio,
  Route,
  ScrollText,
  ShieldAlert,
  Terminal,
  Users,
} from 'lucide-react'
import { type SidebarData } from '../types'

export const sidebarData: SidebarData = {
  user: {
    name: 'Usuario EMS',
    email: 'PUCP · TEL142',
    avatar: '/images/ems-network.svg',
  },
  teams: [
    {
      name: 'MAEstro',
      logo: '/images/logo_final.png',
      plan: 'Open5GS · srsRAN',
    },
  ],
  navGroups: [
    {
      title: 'Operación',
      items: [
        {
          title: 'Resumen',
          url: '/',
          icon: LayoutDashboard,
          color: 'text-sky-500',
          description: 'Panel principal y estado general del cluster 5G',
        },
        {
          title: 'Topología',
          url: '/topology',
          icon: Network,
          color: 'text-blue-500',
          description: 'Grafo interactivo 5G SA y estado de funciones CP/UPF',
        },
        {
          title: 'Alarmas',
          url: '/alarms',
          icon: ShieldAlert,
          color: 'text-amber-500',
          description: 'Monitoreo de fallas activas, criticidad e incidentes',
        },
        {
          title: 'Performance',
          url: '/performance',
          icon: ChartNoAxesCombined,
          color: 'text-emerald-500',
          description: 'Métricas de KPIs, rendimiento, CPU, RAM y tráfico',
        },
        {
          title: 'Analítica NWDAF',
          url: '/nwdaf',
          icon: ChartNoAxesCombined,
          description: 'Pronósticos de carga y experiencia de servicio',
        },
      ],
    },
    {
      title: 'Diagnóstico',
      items: [
        {
          title: 'Traza General E2E',
          url: '/traces/e2e',
          icon: Radio,
          color: 'text-violet-500',
          badge: 'Rel-16',
          description: 'Decodificación 3GPP Release 16 y diagramas de flujo',
        },
        {
          title: 'Trazas en Nodo',
          url: '/traces/node',
          icon: Route,
          color: 'text-fuchsia-500',
          description: 'Capturas locales por NF e interfaz 3GPP',
        },
        {
          title: 'Comandos MML',
          url: '/commands',
          icon: Terminal,
          color: 'text-indigo-500',
          description: 'Consola interactiva y comandos batch para el Core',
        },
        {
          title: 'Laboratorio 5G',
          url: '/laboratory',
          icon: FlaskConical,
          color: 'text-indigo-500',
          description: 'Hipótesis, diseños y planes experimentales reproducibles',
        },
        {
          title: 'Inyección de Fallas',
          url: '/diagnostico/fallas',
          icon: ShieldAlert,
          color: 'text-amber-500',
          description: 'Catálogo e inyección controlada de fallas en funciones 5G',
        },
      ],
    },
    {
      title: 'Servicios',
      items: [
        {
          title: 'Casos Verticales (URLLC & MIoT)',
          url: '/services/verticals',
          icon: Radio,
          color: 'text-teal-500',
          description: 'Movilidad V2X y parque de telemetría masiva',
        },
        {
          title: 'Suscriptores',
          url: '/subscribers',
          icon: Users,
          color: 'text-cyan-500',
          description: 'Gestión de SIMs, perfiles QoS, IMSI y Slices de red',
        },
        {
          title: 'Tarificación 5G',
          url: '/charging',
          icon: Coins,
          color: 'text-amber-500',
          badge: 'Experimental',
          description:
            'Consulta de cuentas, sesiones, CDR y movimientos del CHF',
        },
      ],
    },
    {
      title: 'Sistema',
      items: [
        {
          title: 'Configuración',
          url: '/configuration',
          icon: FileCode2,
          color: 'text-slate-400',
          description: 'Parámetros YAML de Open5GS, APNs y subredes',
        },
        {
          title: 'Auditoría',
          url: '/audit',
          icon: ScrollText,
          color: 'text-zinc-400',
          description: 'Trazabilidad de comandos y registro de operadores',
        },
      ],
    },
  ],
}
