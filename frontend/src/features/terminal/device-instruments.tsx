import { useId } from 'react'

export function Dial({
  value,
  max,
  unit,
  label,
  color = '#70e7d0',
  small = false,
}: {
  value: number
  max: number
  unit: string
  label: string
  color?: string
  small?: boolean
}) {
  const id = useId()
  const point = (angle: number, radius: number) => {
    const a = ((angle - 90) * Math.PI) / 180
    return [150 + radius * Math.cos(a), 150 + radius * Math.sin(a)]
  }
  const arc = (end: number) => {
    const a = point(-130, 119),
      b = point(end, 119)
    return `M${a.join(',')} A119,119 0 ${end + 130 > 180 ? 1 : 0} 1 ${b.join(',')}`
  }
  return (
    <svg
      viewBox='0 0 300 275'
      role='img'
      aria-label={`${label}: ${value.toFixed(small ? 1 : 0)} ${unit}`}
      className='instrument-dial'
    >
      <defs>
        <radialGradient id={id}>
          <stop stopColor='#0e1117' />
          <stop offset='1' stopColor='#020305' />
        </radialGradient>
      </defs>
      <circle
        cx='150'
        cy='150'
        r='134'
        fill={`url(#${id})`}
        stroke='rgba(255, 255, 255, 0.08)'
        strokeWidth='.7'
      />
      <path
        d={arc(130)}
        fill='none'
        stroke='#10141c'
        strokeWidth='5'
        strokeLinecap='round'
      />
      <path
        d={arc(-129.9 + Math.min(1, Math.max(0, value / max)) * 259.9)}
        fill='none'
        stroke={color}
        strokeWidth='5'
        strokeLinecap='round'
      />
      {Array.from({ length: 41 }, (_, i) => {
        const angle = -130 + i * 6.5,
          a = point(angle, 108),
          b = point(angle, i % 5 ? 103 : 97)
        const text = point(angle, 83)
        return (
          <g key={i}>
            <line
              x1={a[0]}
              y1={a[1]}
              x2={b[0]}
              y2={b[1]}
              stroke={i > 34 ? '#efaa68' : '#6b8792'}
              strokeWidth={i % 5 ? 1 : 2}
            />
            {i % 5 === 0 && (
              <text
                x={text[0]}
                y={text[1]}
                textAnchor='middle'
                dominantBaseline='central'
                fill='#8199a4'
                fontSize='10'
              >
                {Math.round((i * max) / 40)}
              </text>
            )}
          </g>
        )
      })}
      <g
        style={{
          transform: `rotate(${-130 + (value / max) * 260}deg)`,
          transformOrigin: '150px 150px',
          transition: 'transform .18s linear',
        }}
      >
        <path d='M148 150 L150 49 L152 150Z' fill={color} opacity='.8' />
      </g>
      <circle cx='150' cy='150' r='45' fill='#040508' stroke='rgba(255, 255, 255, 0.08)' strokeWidth='1' />
      <text
        x='150'
        y='151'
        textAnchor='middle'
        dominantBaseline='central'
        fill='#f1f9fb'
        fontSize={small ? 36 : 53}
        fontWeight='300'
        fontFamily='ui-monospace, monospace'
      >
        {small ? value.toFixed(1) : Math.round(value)}
      </text>
      <text
        x='150'
        y='181'
        textAnchor='middle'
        fill={color}
        fontSize='11'
        letterSpacing='2'
      >
        {unit}
      </text>
      <text
        x='150'
        y='235'
        textAnchor='middle'
        fill='#a6bcc7'
        fontSize='10'
        letterSpacing='2'
      >
        {label}
      </text>
    </svg>
  )
}

export function Trace({
  values,
  color = '#6be9cf',
  label,
}: {
  values: (number | null)[]
  color?: string
  label: string
}) {
  const finite = values.filter((v): v is number => v !== null)
  const max = Math.max(5, ...finite) * 1.15
  return (
    <svg
      viewBox='0 0 400 80'
      className='device-trace'
      role='img'
      aria-label={label}
    >
      {[15, 40, 65].map((y) => (
        <line
          key={y}
          x1='0'
          x2='400'
          y1={y}
          y2={y}
          stroke='rgba(255, 255, 255, 0.08)'
          strokeDasharray='2 5'
        />
      ))}
      {values.map(
        (v, i) =>
          i > 0 &&
          v !== null &&
          values[i - 1] !== null && (
            <line
              key={i}
              x1={((i - 1) * 400) / Math.max(1, values.length - 1)}
              x2={(i * 400) / Math.max(1, values.length - 1)}
              y1={73 - ((values[i - 1] ?? 0) / max) * 65}
              y2={73 - (v / max) * 65}
              stroke={color}
              strokeWidth='2'
            />
          )
      )}
      {values.map(
        (v, i) =>
          v !== null && (
            <circle
              key={`point-${i}`}
              cx={values.length === 1 ? 200 : (i * 400) / (values.length - 1)}
              cy={73 - (v / max) * 65}
              r='2.5'
              fill={color}
            />
          )
      )}
      {!finite.length && (
        <text x='200' y='45' fill='#8ba1ab' fontSize='12' textAnchor='middle'>
          En espera de medición
        </text>
      )}
      <text x='397' y='12' fill='#8ba1ab' fontSize='9' textAnchor='end'>
        {max.toFixed(1)} ms
      </text>
    </svg>
  )
}

export function Road({
  moving,
  braking,
}: {
  moving: boolean
  braking: boolean
}) {
  return (
    <svg
      viewBox='0 0 230 255'
      className={`cockpit-road ${moving ? 'moving' : ''}`}
      role='img'
      aria-label='Escena de conducción simulada'
    >
      <defs>
        <linearGradient id='road-fill' x2='0' y2='1'>
          <stop stopColor='#0a0d14' />
          <stop offset='1' stopColor='#020305' />
        </linearGradient>
      </defs>
      <path d='M97 14 L133 14 L224 255 L6 255Z' fill='url(#road-fill)' />
      <path
        d='M97 14 L6 255 M133 14 L224 255'
        stroke={braking ? '#ff7b7b' : '#58ceb5'}
        opacity='.65'
        fill='none'
      />
      <path
        d='M109 14 L84 255 M121 14 L146 255'
        stroke='#8198a6'
        strokeDasharray='13 17'
        opacity='.4'
        className='road-lanes'
      />
      <path
        d='M115 157 L112 82 L116 47'
        stroke={braking ? '#ff7676' : '#65e8d2'}
        strokeWidth='3'
        fill='none'
        strokeDasharray='4 6'
      />
      {[0, 1, 2].map((n) => (
        <path
          key={n}
          d={`M${86 - n * 9} ${141 - n * 15} Q115 ${120 - n * 18} ${144 + n * 9} ${141 - n * 15}`}
          stroke={braking ? '#f57a79' : '#5acdbb'}
          fill='none'
          opacity={0.65 - n * 0.18}
        />
      ))}
      <g transform='translate(90 155)'>
        <rect x='-4' y='14' width='5' height='17' rx='2' fill='#090d10' />
        <rect x='49' y='14' width='5' height='17' rx='2' fill='#090d10' />
        <rect
          x='0'
          y='0'
          width='50'
          height='79'
          rx='17'
          fill='#9bafb9'
          stroke='#e0edf0'
        />
        <path d='M7 16 Q25 9 43 16 L40 31 L10 31Z' fill='#182d39' />
        <rect x='9' y='35' width='32' height='25' rx='6' fill='#6b838f' />
        <path d='M10 65 L40 65 L42 72 L8 72Z' fill='#20323d' />
        <path
          d='M4 67 L4 74 M46 67 L46 74'
          stroke={braking ? '#ff4949' : '#c56c6c'}
          strokeWidth='4'
        />
      </g>
      <rect
        x='137'
        y='54'
        width='17'
        height='29'
        rx='5'
        fill='#3d596a'
        stroke='#728c9b'
      />
    </svg>
  )
}
