using DesignStudio.Model;
using DesignStudio.Model.Solve;
using Xunit;

namespace DesignStudio.Tests;

// ============================================================================
// Phase S13 — solver scale-out + C++ hot path (E20).
// Exit criterion: a full multi-layer board's controlled nets extract in
// parallel, with progress + cancel, an unchanged net is reused from cache, and
// the dense-solve hot path agrees whether it runs in managed code or the C++ core.
// ============================================================================

public class DenseSolverTests
{
    private static double[,] A3() => new double[,]
    {
        { 2, 1, 1 },
        { 4, -6, 0 },
        { -2, 7, 2 }
    };

    [Fact]
    public void ManagedSolveRecoversKnownSolution()
    {
        bool prev = DenseSolver.UseNative;
        try
        {
            DenseSolver.UseNative = false;                 // force the reference path
            // x = (1, 2, 3) ⇒ b = A·x
            var b = new[] { 7.0, -8.0, 18.0 };
            var x = DenseSolver.SolveColumns(A3(), new[] { b })[0];
            Assert.Equal(1.0, x[0], 9);
            Assert.Equal(2.0, x[1], 9);
            Assert.Equal(3.0, x[2], 9);
        }
        finally { DenseSolver.UseNative = prev; }
    }

    [Fact]
    public void SolveColumnsHandlesMultipleRhs()
    {
        bool prev = DenseSolver.UseNative;
        try
        {
            DenseSolver.UseNative = false;
            var a = new double[,] { { 4, 3 }, { 6, 3 } };
            // two RHS = identity columns ⇒ solutions are the inverse columns
            var inv = DenseSolver.SolveColumns(a, new[] { new[] { 1.0, 0.0 }, new[] { 0.0, 1.0 } });
            // A⁻¹ = 1/det [ 3 -3; -6 4 ], det = -6
            Assert.Equal(3.0 / -6.0, inv[0][0], 9);
            Assert.Equal(-6.0 / -6.0, inv[0][1], 9);
            Assert.Equal(-3.0 / -6.0, inv[1][0], 9);
            Assert.Equal(4.0 / -6.0, inv[1][1], 9);
        }
        finally { DenseSolver.UseNative = prev; }
    }

    // The MoM cross-section solve runs through DenseSolver — managed and (when the
    // core library is present) native must give the same extraction. With no
    // native library both are managed, so this is a clean no-op equality; with it,
    // a real parity check.
    [Fact]
    public void NativeAndManagedExtractionAgree()
    {
        bool prev = DenseSolver.UseNative;
        try
        {
            DenseSolver.UseNative = false;
            var man = MoM2D.Microstrip(0.2, 0.035, 0.2, 4.3);
            DenseSolver.UseNative = true;
            var nat = MoM2D.Microstrip(0.2, 0.035, 0.2, 4.3);
            Assert.Equal(man.Z0, nat.Z0, 6);
            Assert.Equal(man.EpsEff, nat.EpsEff, 6);
            Assert.True(man.Z0 > 0 && man.EpsEff > 1 && man.EpsEff < 4.3);
        }
        finally { DenseSolver.UseNative = prev; }
    }
}

public class SweepSchedulerTests
{
    [Fact]
    public void PreservesInputOrderUnderParallelism()
    {
        var items = Enumerable.Range(0, 200).ToList();
        var res = SweepScheduler.Run(items, i => i * i);
        for (int i = 0; i < items.Count; i++) Assert.Equal(i * i, res[i]);
    }

    [Fact]
    public void ReportsProgressToCompletion()
    {
        var items = Enumerable.Range(0, 50).ToList();
        int maxSeen = 0, total = 0;
        // Progress<T> posts to the captured SynchronizationContext asynchronously,
        // so use a synchronous sink to observe completion deterministically.
        var sink = new SyncProgress(p => { maxSeen = Math.Max(maxSeen, p.Completed); total = p.Total; });
        SweepScheduler.Run(items, i => i, sink);
        Assert.Equal(50, total);
        Assert.Equal(50, maxSeen);
    }

    [Fact]
    public void CancelledTokenStopsTheSweep()
    {
        var items = Enumerable.Range(0, 1000).ToList();
        using var cts = new System.Threading.CancellationTokenSource();
        cts.Cancel();
        Assert.ThrowsAny<OperationCanceledException>(() =>
            SweepScheduler.Run(items, i => i * 2, ct: cts.Token));
    }

    private sealed class SyncProgress : IProgress<SweepProgress>
    {
        private readonly Action<SweepProgress> _on;
        public SyncProgress(Action<SweepProgress> on) => _on = on;
        public void Report(SweepProgress value) { lock (_on) _on(value); }
    }
}

public class ExtractionCacheTests
{
    [Fact]
    public void HitsOnSameVersionMissesAfterBump()
    {
        var cache = new ExtractionCache<int>();
        int computes = 0;
        int Compute() { computes++; return 42; }

        Assert.Equal(42, cache.GetOrAdd(1, 7, "grid", Compute));   // miss
        Assert.Equal(42, cache.GetOrAdd(1, 7, "grid", Compute));   // hit
        Assert.Equal(1, computes);
        Assert.Equal(1L, cache.Hits);
        Assert.Equal(1L, cache.Misses);

        Assert.Equal(42, cache.GetOrAdd(2, 7, "grid", Compute));   // version bump ⇒ miss
        Assert.Equal(2, computes);
        Assert.Equal(2L, cache.Misses);
    }

    [Fact]
    public void DifferentGridsGetDistinctSignatures()
    {
        string a = ExtractionCache<int>.GridSignature(new[] { 1e8, 2e8, 3e8 });
        string b = ExtractionCache<int>.GridSignature(new[] { 1e8, 2e8, 3.0001e8 });
        string c = ExtractionCache<int>.GridSignature(new[] { 1e8, 2e8, 3e8 });
        Assert.NotEqual(a, b);
        Assert.Equal(a, c);
    }
}

public class ParallelExtractTests
{
    private static BoardDocument Board(out int[] nets)
    {
        var doc = new BoardDocument();
        doc.SetBoardSize(60, 40);
        doc.SetCopperLayers(4);
        doc.Stackup[0].MaterialId = "megtron6";
        int n0 = doc.AddNet("S0");
        int n1 = doc.AddNet("S1");
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 10, Bx = 25, By = 10, Width = 0.15, NetId = n0, Layer = 0 });
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 20, Bx = 25, By = 20, Width = 0.15, NetId = n1, Layer = 0 });
        nets = new[] { n0, n1 };
        return doc;
    }

    private static double[] Freqs() => new[] { 1e8, 5e9, 1e10, 2e10 };

    [Fact]
    public void ParallelExtractMatchesSerial()
    {
        using var doc = Board(out var nets);
        var freqs = Freqs();
        var map = ChannelExtractor.ExtractManyParallel(doc, nets, freqs);

        foreach (int net in nets)
        {
            var serial = ChannelExtractor.ExtractAt(doc, net, freqs);
            var par = map[net];
            Assert.Equal(serial.Count, par.Count);
            for (int k = 0; k < serial.Count; k++)
            {
                Assert.Equal(serial[k].S21.Magnitude, par[k].S21.Magnitude, 9);
                Assert.Equal(serial[k].S21.Phase, par[k].S21.Phase, 9);
            }
        }
    }

    [Fact]
    public void CacheReusesUnchangedNetsAndRecomputesAfterEdit()
    {
        using var doc = Board(out var nets);
        var freqs = Freqs();
        var cache = new ExtractionCache<List<ChannelExtractor.FreqPoint>>();

        ChannelExtractor.ExtractManyParallel(doc, nets, freqs, cache);
        long missesAfterFirst = cache.Misses;
        Assert.Equal((long)nets.Length, missesAfterFirst);    // every net computed once

        ChannelExtractor.ExtractManyParallel(doc, nets, freqs, cache);
        Assert.Equal(missesAfterFirst, cache.Misses);         // nothing changed ⇒ all hits
        Assert.True(cache.Hits >= nets.Length);

        // an edit bumps BoardDocument.Version ⇒ the cache invalidates
        doc.AddNet("S2");
        doc.NotifyChanged();
        ChannelExtractor.ExtractManyParallel(doc, nets, freqs, cache);
        Assert.True(cache.Misses > missesAfterFirst, "a document edit must invalidate the cache");
    }
}
