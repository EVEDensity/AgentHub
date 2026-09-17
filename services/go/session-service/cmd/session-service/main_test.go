package main

import "testing"

func TestNormalizeListLimit(t *testing.T) {
	tests := map[string]int{
		"":    10,
		"bad": 10,
		"0":   10,
		"-5":  10,
		"1":   1,
		"50":  50,
		"51":  50,
	}
	for raw, want := range tests {
		if got := normalizeListLimit(raw); got != want {
			t.Fatalf("normalizeListLimit(%q) = %d, want %d", raw, got, want)
		}
	}
}
