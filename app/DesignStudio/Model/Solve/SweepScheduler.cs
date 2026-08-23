namespace DesignStudio.Model.Solve;

// ============================================================================
// E20. Multithreaded sweep scheduler.
//
// A 24-layer board's controlled nets are thousands of independent extractions
// (each net × each frequency), every one a self-contained dense solve. That is
// embarrassingly parallel, so the throughput win is simply running them across
// all cores with ordered results, live progress and cooperative cancel — the
// difference between "minutes" and "the weekend" in the roadmap exit criterion.
//
// Results are written by input index, so the output order is deterministic
// regardless of completion order. Cancellation is cooperative: a cancelled token
// stops scheduling new work and surfaces an OperationCanceledException.
// ============================================================================

public readonly record struct SweepProgress(int Completed, int Total)
{
    public double Fraction => Total > 0 ? (double)Completed / Total : 1.0;
}

public static class SweepScheduler
{
    /// <summary>
    /// Run <paramref name="work"/> over every item in parallel, returning the
    /// results in input order. Reports progress as each item completes and
    /// honours <paramref name="ct"/>.
    /// </summary>
    /// <param name="maxDegree">0 ⇒ Environment.ProcessorCount.</param>
    public static T[] Run<TIn, T>(IReadOnlyList<TIn> items, Func<TIn, T> work,
                                  IProgress<SweepProgress>? progress = null,
                                  CancellationToken ct = default, int maxDegree = 0)
    {
        int total = items.Count;
        var results = new T[total];
        if (total == 0) return results;

        int done = 0;
        var opts = new ParallelOptions
        {
            CancellationToken = ct,
            MaxDegreeOfParallelism = maxDegree > 0 ? maxDegree : Environment.ProcessorCount
        };

        Parallel.For(0, total, opts, i =>
        {
            opts.CancellationToken.ThrowIfCancellationRequested();
            results[i] = work(items[i]);
            int c = Interlocked.Increment(ref done);
            progress?.Report(new SweepProgress(c, total));
        });

        return results;
    }

    /// <summary>
    /// Run over items keyed by an id, returning an id→result map. Convenience for
    /// per-net board sweeps where the caller wants lookup by net id rather than
    /// position.
    /// </summary>
    public static Dictionary<TKey, T> RunKeyed<TKey, TIn, T>(
        IReadOnlyList<(TKey key, TIn input)> items, Func<TIn, T> work,
        IProgress<SweepProgress>? progress = null, CancellationToken ct = default,
        int maxDegree = 0) where TKey : notnull
    {
        var inputs = items.Select(p => p.input).ToList();
        var res = Run(inputs, work, progress, ct, maxDegree);
        var map = new Dictionary<TKey, T>(items.Count);
        for (int i = 0; i < items.Count; i++) map[items[i].key] = res[i];
        return map;
    }
}
