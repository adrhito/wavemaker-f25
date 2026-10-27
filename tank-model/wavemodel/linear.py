"""Linear water-wave relations used by the tank model."""

from __future__ import annotations

import numpy as np

G = 9.81          # m/s^2
NU = 1.0e-6       # m^2/s, water near 20 C


def wavenumber(omega, h):
    """k from omega^2 = g k tanh(k h), vectorised (Newton from Fenton's start)."""
    omega = np.asarray(omega, float)
    k0 = omega ** 2 / G
    with np.errstate(divide="ignore", invalid="ignore"):
        k = np.where(omega > 0, k0 / np.sqrt(np.tanh(k0 * h)), 0.0)
        for _ in range(30):
            th = np.tanh(k * h)
            f = G * k * th - omega ** 2
            df = G * th + G * k * h * (1 - th ** 2)
            k = np.where(omega > 0, k - f / df, 0.0)
    return k


def wavenumber_complex(omega, h):
    """k for a complex frequency (used by the exponential-window FFT), Newton from the real root."""
    omega = complex(omega)
    if abs(omega.real) < 1e-9:
        # purely imaginary frequency: k is imaginary, about i sigma / sqrt(g h)
        k = 1j * abs(omega.imag) / np.sqrt(G * h)
    else:
        k = complex(float(wavenumber(abs(omega.real), h)))
    for _ in range(40):
        th = np.tanh(k * h)
        f = G * k * th - omega ** 2
        df = G * th + G * k * h * (1 - th ** 2)
        k = k - f / df
    return k


def phase_speed(omega, h):
    k = wavenumber(omega, h)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(k > 0, omega / k, np.sqrt(G * h))


def group_speed(omega, h):
    k = wavenumber(omega, h)
    c = phase_speed(omega, h)
    kh = k * h
    with np.errstate(divide="ignore", invalid="ignore"):
        n = np.where(kh > 1e-9, 0.5 * (1 + 2 * kh / np.sinh(2 * kh)), 1.0)
    return n * c


def effective_depth(k, h):
    """tanh(kh)/k: the depth that makes the shallow-water flux relation exact for linear waves."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(k * h > 1e-9, np.tanh(k * h) / k, h)


def hunt_damping(omega, h, width, nu=NU):
    """Spatial amplitude decay rate (1/m) from laminar boundary layers on the
    bed and both side walls of a channel of `width` (Hunt 1952)."""
    k = wavenumber(omega, h)
    kh = k * h
    with np.errstate(divide="ignore", invalid="ignore"):
        a = (2 * k / width) * np.sqrt(nu / (2 * np.maximum(omega, 1e-9))) * \
            (k * width + np.sinh(2 * kh)) / (2 * kh + np.sinh(2 * kh))
    return np.where(omega > 0, a, 0.0)


def source_coupling(k, h, z_top):
    """Propagating-wave amplitude from a horizontal flux confined to the
    bottom layer 0 < z < z_top, relative to the depth-averaged model
    (which is exact for long waves). Linear wavemaker theory:

        4 sinh(kh) sinh(k z_top) / (k z_top (2kh + sinh 2kh))

    It tends to 1 for long waves and falls off for short ones, because a
    source near the bed cannot shake the surface of short waves.
    """
    z = np.minimum(z_top, h)
    kh = k * h
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        g = 4 * np.sinh(kh) * np.sinh(k * z) / (k * z * (2 * kh + np.sinh(2 * kh)))
    return np.where(np.real(k) * h > 1e-6, g, 1.0)


def stokes2_crest(a, k, h):
    """(crest, trough) of a second-order Stokes wave of first-order amplitude a."""
    kh = k * h
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        a2 = (k * a ** 2 / 4) * np.cosh(kh) * (2 + np.cosh(2 * kh)) / np.sinh(kh) ** 3
    a2 = np.where(np.isfinite(a2), a2, 0.0)
    return a + a2, a - a2


def ursell(H, k, h):
    """Ursell number H L^2 / h^3: above ~26 the wave is cnoidal and linear theory underpredicts crests."""
    L = 2 * np.pi / np.maximum(k, 1e-9)
    return H * L ** 2 / h ** 3


def breaking_height(k, h):
    """Miche limit H_max = 0.142 L tanh(kh), capped at the depth-limited 0.78 h."""
    L = 2 * np.pi / np.maximum(k, 1e-9)
    return np.minimum(0.142 * L * np.tanh(k * h), 0.78 * h)
