/// <reference types="vite/client" />

declare namespace React {
  namespace JSX {
    interface IntrinsicElements {
      'model-viewer': React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement> & {
        src?: string
        alt?: string
        'auto-rotate'?: boolean
        'camera-controls'?: boolean
        autoplay?: boolean
        'shadow-intensity'?: string | number
        'shadow-softness'?: string | number
        exposure?: string | number
        'camera-orbit'?: string
        'min-camera-orbit'?: string
        'max-camera-orbit'?: string
        'camera-target'?: string
        'field-of-view'?: string
        'animation-name'?: string
        loading?: 'auto' | 'lazy' | 'eager'
        reveal?: 'auto' | 'interaction' | 'manual'
        style?: React.CSSProperties
        class?: string
        id?: string
      }
    }
  }
}
