# ============================================================
#  tests/fixtures/quarto_diagram_fixtures.py
#  Standardized Test Fixtures for Quarto Technical Diagrams (ADR-005)
# ============================================================

"""
Authoritative test fixtures specified in Section 16 of the Quarto
Pipeline Specification:
- Fixture A: Simple TikZ system block diagram
- Fixture B: PGFPlots function parabola with marked coordinates
- Fixture C: Discrete-time ycomb signal plot
- Fixture D: Bilingual Persian/English diagram with mathematical formulas
- Fixture E: Multiple independent diagrams in a single document
- Fixture F: Invalid and incomplete diagrams for error handling
- Fixture G: Full mixed document combining headings, Persian prose, math,
             lists, tables, code blocks, callouts, and diagrams
"""

FIXTURE_A_SIMPLE_TIKZ = r"""```{=latex}
\begin{tikzpicture}[auto, node distance=2cm, >=latex]
    \node [draw, rectangle, minimum height=1cm, minimum width=2cm] (sys) {System $H(s)$};
    \draw [->] (-2,0) -- node {$x(t)$} (sys);
    \draw [->] (sys) -- node {$y(t)$} (2,0);
\end{tikzpicture}
```"""

FIXTURE_B_PGFPLOTS_FUNCTION = r"""```{=latex}
\begin{tikzpicture}
    \begin{axis}[
        xlabel={$x$},
        ylabel={$y = x^2$},
        grid=major,
        width=8cm,
        height=6cm
    ]
        \addplot[blue, thick, domain=-2:2, samples=50] {x^2};
        \node[label={above:$(0,0)$},circle,fill,inner sep=2pt] at (axis cs:0,0) {};
        \node[label={left:$(-1,1)$},circle,fill,inner sep=2pt] at (axis cs:-1,1) {};
        \node[label={right:$(1,1)$},circle,fill,inner sep=2pt] at (axis cs:1,1) {};
    \end{axis}
\end{tikzpicture}
```"""

FIXTURE_C_DISCRETE_SIGNAL_YCOMB = r"""```{=latex}
\begin{tikzpicture}
    \begin{axis}[
        xlabel={$n$},
        ylabel={$x[n]$},
        ycomb,
        width=8cm,
        height=5cm,
        xmin=-3, xmax=4,
        ymin=-1, ymax=3
    ]
        \addplot+[thick, mark=*] coordinates {(-2, 0) (-1, 1) (0, 2) (1, 1.5) (2, 0.5) (3, 0)};
    \end{axis}
\end{tikzpicture}
```"""

FIXTURE_D_PERSIAN_ENGLISH_DIAGRAM = r"""# تحلیل سیستم کنترل پس‌خور

سیستم نشان داده شده در شکل زیر یک حلقه کنترلی فیدبک واحد را نمایش می‌دهد:

```{=latex}
\begin{tikzpicture}[auto, node distance=2.5cm, >=latex]
    \node [draw, circle] (sum) {$\Sigma$};
    \node [draw, rectangle, right of=sum] (controller) {$G(s)$};
    \node [right of=controller, node distance=2cm] (output) {};
    \node [left of=sum, node distance=2cm] (input) {};

    \draw [->] (input) -- node {$R(s)$} (sum);
    \draw [->] (sum) -- node {$E(s)$} (controller);
    \draw [->] (controller) -- node [name=y] {$Y(s)$} (output);
    \draw [->] (y) -- ++(0,-1.5) -| node [pos=0.99] {$-$} (sum);
\end{tikzpicture}
```

تابع تبدیل حلقه بسته به صورت زیر محاسبه می‌شود:
$$T(s) = \frac{Y(s)}{R(s)} = \frac{G(s)}{1 + G(s)}$$
"""

FIXTURE_E_MULTIPLE_DIAGRAMS = r"""# مدار و منحنی مشخصه

## دیاگرام مدار الکتریکی

```{=latex}
\begin{circuitikz}[american]
    \draw (0,0) to[V=$V_s$] (0,2) to[R=$R$] (2,2) to[C=$C$] (2,0) -- (0,0);
\end{circuitikz}
```

## پاسخ گذرا

```{=latex}
\begin{tikzpicture}
    \begin{axis}[xlabel={$t$}, ylabel={$v_C(t)$}, width=7cm, height=4.5cm]
        \addplot[domain=0:5, samples=30, red, thick] {1 - exp(-x)};
    \end{axis}
\end{tikzpicture}
```
"""

FIXTURE_F_INVALID_DIAGRAMS = r"""# آزمایش خطاهای دیاگرام

این بخش شامل دیاگرام ناقص در هنگام تایپ است:

```{=latex}
\begin{tikzpicture}
    \draw (0,0) -- (1,1);
```

و دیاگرام با دستور نامعتبر در بدنه:

```{=latex}
\begin{tikzpicture}
    \invalidTeXCommandHereXYZ{}
\end{tikzpicture}
```
"""

FIXTURE_G_MIXED_DOCUMENT = r"""---
title: "گزارش جامع پردازش سیگنال و مدارهای الکتریکی"
author: "سامانه پُلپو"
---

# ۱. مقدمه و مبانی ریاضی

این یک سند آزمایشی کامل برای اعتبارسنجی یکپارچگی رندرینگ و پیش‌نمایش پُلپو است.

تبدیل فوریه سیگنال پیوسته به صورت رابطه زیر تعریف می‌شود:
$$X(j\omega) = \int_{-\infty}^{\infty} x(t) e^{-j\omega t} dt$$

در حالت گسسته، متغیر $z$ در رابطه $X(z) = \sum_{n=-\infty}^{\infty} x[n] z^{-n}$ استفاده می‌شود.

> فرکانس نمونه‌برداری بر اساس قضیه نایکویست باید حداقل دو برابر حداکثر فرکانس سیگنال باشد ($f_s \ge 2 f_{max}$).

# ۲. مدار الکتریکی و فیلتر پایین‌گذر

مدار فیلتر RC با ساختار زیر تعریف می‌شود:

```{=latex}
\begin{circuitikz}[american]
    \draw (0,0) to[sV=$v_i(t)$] (0,2) to[R=$1\,\text{k}\Omega$] (2.5,2)
          to[C=$1\,\mu\text{F}$] (2.5,0) -- (0,0);
    \draw (2.5,2) -- (3.5,2);
    \draw (2.5,0) -- (3.5,0);
    \draw (3.5,2) to[open, v^=$v_o(t)$] (3.5,0);
\end{circuitikz}
```

# ۳. پاسخ فرکانسی سیستم

منحنی زیر پاسخ دامنه فیلتر را نمایش می‌دهد:

```{=latex}
\begin{tikzpicture}
    \begin{axis}[
        xlabel={$\omega / \omega_c$},
        ylabel={$|H(j\omega)|$},
        grid=major,
        width=7.5cm,
        height=5cm
    ]
        \addplot[domain=0.1:4, samples=40, blue, thick] {1 / sqrt(1 + x^2)};
    \end{axis}
\end{tikzpicture}
```

# ۴. مشخصات قطعات مدار

| نام قطعه | مقدار اسمی | تلورانس |
| :--- | :--- | :--- |
| مقاومت $R_1$ | $1\,\text{k}\Omega$ | $1\%$ |
| خازن $C_1$ | $1\,\mu\text{F}$ | $5\%$ |

# ۵. کد شبیه‌سازی عددی

قطعه کد پایتون زیر برای محاسبه پاسخ فرکانسی استفاده می‌شود:

```python
import numpy as np

w = np.logspace(0, 5, 1000)
H = 1.0 / (1.0 + 1j * w * 1e-3)
magnitude = 20 * np.log10(np.abs(H))
```

* گام اول: تعریف بردار فرکانس
* گام دوم: محاسبه تابع تبدیل
* گام سوم: ترسیم نمودار بود

پایان گزارش.
"""
