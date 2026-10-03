import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, type RenderResult } from 'vitest-browser-react'
import { type Locator, userEvent } from 'vitest/browser'
import { UserAuthForm } from './user-auth-form'

const { navigate, setUser, setAccessToken, post } = vi.hoisted(() => ({
  navigate: vi.fn(),
  setUser: vi.fn(),
  setAccessToken: vi.fn(),
  post: vi.fn(),
}))

vi.mock('@/stores/auth-store', () => ({
  useAuthStore: () => ({ auth: { setUser, setAccessToken } }),
}))
vi.mock('@/lib/api', () => ({ api: { post } }))
vi.mock('@tanstack/react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@tanstack/react-router')>()),
  useNavigate: () => navigate,
}))

describe('UserAuthForm', () => {
  let screen: RenderResult
  let username: Locator
  let password: Locator
  let submit: Locator

  beforeEach(async () => {
    vi.clearAllMocks()
    post.mockResolvedValue({
      data: {
        access_token: 'jwt-token',
        user: { username: 'docente', role: 'teacher', testbed: null },
      },
    })
    screen = await render(<UserAuthForm />)
    username = screen.getByRole('textbox', { name: /^Usuario$/i })
    password = screen.getByLabelText(/^Contraseña$/i)
    submit = screen.getByRole('button', { name: /^Ingresar$/i })
  })

  it('envía las credenciales al backend y conserva la sesión', async () => {
    await userEvent.clear(username)
    await userEvent.fill(username, 'docente')
    await userEvent.clear(password)
    await userEvent.fill(password, 'segura123')
    await userEvent.click(submit)

    await vi.waitFor(() =>
      expect(post).toHaveBeenCalledWith('/auth/login', {
        username: 'docente',
        password: 'segura123',
      })
    )
    expect(setUser).toHaveBeenCalledWith({
      username: 'docente',
      role: 'teacher',
      testbed: null,
    })
    expect(setAccessToken).toHaveBeenCalledWith('jwt-token')
    expect(navigate).toHaveBeenCalledWith({ to: '/', replace: true })
  })

  it('rechaza un usuario vacío antes de llamar la API', async () => {
    await userEvent.clear(username)
    await userEvent.click(submit)
    await expect
      .element(screen.getByText('Ingrese su usuario.'))
      .toBeInTheDocument()
    expect(post).not.toHaveBeenCalled()
  })
})
