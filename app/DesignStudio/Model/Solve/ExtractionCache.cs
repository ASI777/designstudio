using System.Collections.Concurrent;

namespace DesignStudio.Model.Solve;

// ============================================================================
// E20. Board-wide extraction result cache.
//
// Re-running the SI engines over a board that hasn't changed is pure waste:
// re-routing one net, tweaking one decap or re-running a what-if leaves most of
// the controlled nets byte-for-byte identical. This caches per-net extraction
// results keyed on the net id, the frequency-grid signature and the document's
// monotonic `BoardDocument.Version`. A version bump (any edit) makes the stale
// entry miss and recompute; an unchanged net is a hit. Thread-safe, so it backs
// the parallel sweep directly.
// ============================================================================

public sealed class ExtractionCache<T>
{
    private readonly ConcurrentDictionary<string, (long version, T value)> _store = new();
    private long _hits, _misses;

    public long Hits => Interlocked.Read(ref _hits);
    public long Misses => Interlocked.Read(ref _misses);
    public int Count => _store.Count;

    public void Clear() { _store.Clear(); Interlocked.Exchange(ref _hits, 0); Interlocked.Exchange(ref _misses, 0); }

    /// <summary>
    /// Return the cached result for (netId, gridSig) if it was computed at the
    /// current <paramref name="version"/>; otherwise run <paramref name="compute"/>,
    /// store it (overwriting any stale-version entry) and return it.
    /// </summary>
    public T GetOrAdd(long version, int netId, string gridSig, Func<T> compute)
    {
        string key = netId + "|" + gridSig;
        if (_store.TryGetValue(key, out var hit) && hit.version == version)
        {
            Interlocked.Increment(ref _hits);
            return hit.value;
        }
        Interlocked.Increment(ref _misses);
        T value = compute();
        _store[key] = (version, value);   // replaces a stale-version entry in place
        return value;
    }

    /// <summary>Stable signature of a frequency grid (length + endpoints + a
    /// 64-bit FNV-1a over the raw bit patterns), so different grids never collide.</summary>
    public static string GridSignature(double[] freqs)
    {
        ulong h = 1469598103934665603UL;             // FNV-1a offset basis
        foreach (double f in freqs)
        {
            ulong bits = (ulong)BitConverter.DoubleToInt64Bits(f);
            for (int b = 0; b < 8; b++)
            {
                h ^= (bits >> (b * 8)) & 0xFF;
                h *= 1099511628211UL;                 // FNV prime
            }
        }
        double lo = freqs.Length > 0 ? freqs[0] : 0;
        double hi = freqs.Length > 0 ? freqs[^1] : 0;
        return $"{freqs.Length}:{lo:R}:{hi:R}:{h:x}";
    }
}
