package main

import (
	"math"
	"math/rand"
	"sort"
)

// ── Welch's t-test (unequal variance) ──────────────────────────────────
//
// Returns t-statistic, two-sided p-value, and degrees of freedom.
// Uses the standard Welch-Satterthwaite approximation for df and the
// regularised incomplete beta function for the p-value.

func ttestWelch(a, b []float64) (tStat, pValue, df float64) {
	na, nb := float64(len(a)), float64(len(b))
	if na < 2 || nb < 2 {
		return 0, 1, 0
	}

	ma, va := meanVar(a)
	mb, vb := meanVar(b)

	se := math.Sqrt(va/na + vb/nb)
	if se == 0 {
		// Both groups have zero variance — no difference to detect.
		return 0, 1, 0
	}

	tStat = (ma - mb) / se

	// Welch-Satterthwaite degrees of freedom
	num := (va/na + vb/nb) * (va/na + vb/nb)
	den := (va/na)*(va/na)/(na-1) + (vb/nb)*(vb/nb)/(nb-1)
	if den == 0 {
		return tStat, 0, 0
	}
	df = num / den

	// Two-sided p-value via the regularised incomplete beta function.
	pValue = tDistPValue(math.Abs(tStat), df)
	if pValue > 1 {
		pValue = 1
	}

	return tStat, pValue, df
}

// meanVar returns the sample mean and sample variance of xs.
func meanVar(xs []float64) (mean, variance float64) {
	n := float64(len(xs))
	if n == 0 {
		return 0, 0
	}
	s := 0.0
	for _, x := range xs {
		s += x
	}
	mean = s / n
	if n < 2 {
		return mean, 0
	}
	s2 := 0.0
	for _, x := range xs {
		d := x - mean
		s2 += d * d
	}
	variance = s2 / (n - 1)
	return
}

// tDistPValue returns the two-sided p-value for |t| with df degrees of freedom
// using the relationship between the t-distribution and the beta function:
//
//	P(T > |t|) = I_{df/(df+t^2)}(df/2, 1/2)  (one-sided)
//
// Two-sided = 2 * one-sided.
func tDistPValue(t, df float64) float64 {
	x := df / (df + t*t)
	a := df / 2.0
	b := 0.5
	return regIncompleteBeta(x, a, b)
}

// ── Regularised incomplete beta function (continued fraction) ──────────
//
// Computes I_x(a,b) using the continued fraction representation (Lentz's
// algorithm), which is stable even for extreme parameters.

func regIncompleteBeta(x, a, b float64) float64 {
	if x < 0 || x > 1 {
		if x < 0 {
			return 0
		}
		return 1
	}
	if x == 0 || x == 1 {
		return x
	}

	// Use the relationship I_x(a,b) = 1 - I_{1-x}(b,a) to keep x small
	// when possible (improves convergence).
	if x > 0.5 {
		return 1 - regIncompleteBeta(1-x, b, a)
	}

	// Front factor: x^a * (1-x)^b / (a * Beta(a,b))
	front := math.Exp(a*math.Log(x) + b*math.Log(1-x) - logBeta(a, b)) / a

	// Continued fraction evaluation via modified Lentz.
	cf := lentzCF(x, a, b)
	return front * cf
}

// logBeta returns ln(Beta(a,b)).
func logBeta(a, b float64) float64 {
	lgA, _ := math.Lgamma(a)
	lgB, _ := math.Lgamma(b)
	lgAB, _ := math.Lgamma(a + b)
	return lgA + lgB - lgAB
}

// lentzCF evaluates the continued fraction for the regularised incomplete
// beta function using the modified Lentz algorithm.
func lentzCF(x, a, b float64) float64 {
	const eps = 1e-15
	const maxIter = 200

	ab := a + b

	// f0
	f := 1.0
	c := 1.0
	d := 1.0

	d = 1.0 / (1.0 - ab*x/(a+1.0))
	if math.Abs(d) < eps {
		d = eps
	}
	c = 1.0 - ab*x/(a+1.0)
	if math.Abs(c) < eps {
		c = eps
	}
	d = 1.0 / d
	delta := d * c
	f *= delta

	for m := 1; m < maxIter; m++ {
		m2 := float64(2 * m)

		// Even term d_{2m}
		num := float64(m) * (b - float64(m)) * x / ((a + m2 - 1) * (a + m2))
		d = 1.0 + num*d
		if math.Abs(d) < eps {
			d = eps
		}
		c = 1.0 + num/c
		if math.Abs(c) < eps {
			c = eps
		}
		d = 1.0 / d
		delta = d * c
		f *= delta

		// Odd term d_{2m+1}
		num = -(a + float64(m)) * (ab + float64(m)) * x / ((a + m2) * (a + m2 + 1))
		d = 1.0 + num*d
		if math.Abs(d) < eps {
			d = eps
		}
		c = 1.0 + num/c
		if math.Abs(c) < eps {
			c = eps
		}
		d = 1.0 / d
		delta = d * c
		f *= delta

		if math.Abs(delta-1.0) < 1e-12 {
			return f
		}
	}
	return f
}

// ── Mann-Whitney U test ────────────────────────────────────────────────
//
// Non-parametric rank-sum test. Returns U-statistic and approximate
// two-sided p-value via the normal approximation (with continuity
// correction).

func mannWhitneyU(a, b []float64) (uStat, pValue float64) {
	na, nb := len(a), len(b)
	if na == 0 || nb == 0 {
		return 0, 1
	}

	// Build sorted combined list with group labels.
	type pair struct {
		val   float64
		group int // 0 for a, 1 for b
	}
	combined := make([]pair, 0, na+nb)
	for _, v := range a {
		combined = append(combined, pair{v, 0})
	}
	for _, v := range b {
		combined = append(combined, pair{v, 1})
	}
	sort.Slice(combined, func(i, j int) bool { return combined[i].val < combined[j].val })

	// Assign ranks (average rank for ties).
	rankSumA := 0.0
	rankSumB := 0.0
	i := 0
	for i < len(combined) {
		j := i + 1
		for j < len(combined) && combined[j].val == combined[i].val {
			j++
		}
		avgRank := (float64(i+j-1) / 2.0) + 1.0 // +1 because ranks start at 1
		for k := i; k < j; k++ {
			if combined[k].group == 0 {
				rankSumA += avgRank
			} else {
				rankSumB += avgRank
			}
		}
		i = j
	}

	// U statistics.
	naF, nbF := float64(na), float64(nb)
	uA := rankSumA - naF*(naF+1)/2.0
	uB := rankSumB - nbF*(nbF+1)/2.0
	uStat = math.Min(uA, uB)

	// Normal approximation (two-sided).
	mu := naF * nbF / 2.0
	sigma := math.Sqrt(naF * nbF * (naF + nbF + 1) / 12.0)

	if sigma == 0 {
		return uStat, 1
	}

	// Apply continuity correction (0.5) for the smaller U.
	z := (uStat - mu + 0.5) / sigma
	if z < 0 {
		z = -z
	} else {
		z = -(z - 1.0) // use continuity correction symmetrically
		if z < 0 {
			z = -z
		}
	}
	// Actually, standard continuity correction for two-sided:
	z = (math.Abs(uStat-mu) - 0.5) / sigma
	if z < 0 {
		z = 0
	}

	// Two-sided p-value from normal distribution: 2 * (1 - Phi(|z|))
	pValue = 2.0 * (1.0 - normCDF(z))

	if pValue > 1 {
		pValue = 1
	}
	if pValue < 0 {
		pValue = 0
	}

	return uStat, pValue
}

// normCDF returns the standard normal cumulative distribution function
// via math.Erfc.
func normCDF(x float64) float64 {
	return 0.5 * math.Erfc(-x/math.Sqrt2)
}

// ── Bootstrap confidence interval ──────────────────────────────────────

func bootstrapCI(values []float64, nResamples int, alpha float64) (lower, upper float64) {
	if len(values) == 0 {
		return 0, 0
	}
	if nResamples <= 0 {
		nResamples = 1000
	}
	if alpha <= 0 || alpha >= 1 {
		alpha = 0.05
	}

	n := len(values)
	means := make([]float64, nResamples)
	rng := rand.New(rand.NewSource(42)) // deterministic for reproducibility

	for i := 0; i < nResamples; i++ {
		sum := 0.0
		for j := 0; j < n; j++ {
			idx := rng.Intn(n)
			sum += values[idx]
		}
		means[i] = sum / float64(n)
	}

	sort.Float64s(means)

	loIdx := int(float64(nResamples) * alpha / 2.0)
	hiIdx := int(float64(nResamples) * (1.0 - alpha/2.0))
	if loIdx < 0 {
		loIdx = 0
	}
	if hiIdx >= nResamples {
		hiIdx = nResamples - 1
	}

	return means[loIdx], means[hiIdx]
}

// ── Cohen's d (effect size) ────────────────────────────────────────────

func cohensD(a, b []float64) float64 {
	na, nb := float64(len(a)), float64(len(b))
	if na < 1 || nb < 1 {
		return 0
	}
	_, va := meanVar(a)
	_, vb := meanVar(b)

	// Pooled standard deviation (Hedges' g uses n-1 which we already
	// have from meanVar).
	pooledVar := ((na-1)*va + (nb-1)*vb) / (na + nb - 2)
	if pooledVar <= 0 {
		if va == 0 && vb == 0 {
			return 0
		}
		// Fall back to root-mean-square of individual SDs.
		pooledVar = (va + vb) / 2.0
		if pooledVar <= 0 {
			return 0
		}
	}
	pooledSD := math.Sqrt(pooledVar)

	ma, mb := mean(a), mean(b)
	return (ma - mb) / pooledSD
}

// Note: mean is provided by eval_runner.go in the same package.
