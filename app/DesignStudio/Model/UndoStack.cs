namespace DesignStudio.Model;

/// <summary>
/// Generic undo/redo stack. The action has ALREADY been performed when it is
/// pushed; Undo()/Redo() invoke the stored closures. Every mutation in the app
/// goes through this so Ctrl+Z always works.
/// </summary>
public class UndoStack
{
    private sealed record Entry(string Name, Action Undo, Action Redo);

    private readonly Stack<Entry> _undo = new();
    private readonly Stack<Entry> _redo = new();

    public event Action? Changed;

    public bool CanUndo => _undo.Count > 0;
    public bool CanRedo => _redo.Count > 0;
    public string? NextUndoName => _undo.Count > 0 ? _undo.Peek().Name : null;
    public string? NextRedoName => _redo.Count > 0 ? _redo.Peek().Name : null;

    /// <summary>Record an already-performed action.</summary>
    public void Push(string name, Action undo, Action redo)
    {
        _undo.Push(new Entry(name, undo, redo));
        _redo.Clear();
        Changed?.Invoke();
    }

    public void Undo()
    {
        if (_undo.Count == 0) return;
        var e = _undo.Pop();
        e.Undo();
        _redo.Push(e);
        Changed?.Invoke();
    }

    public void Redo()
    {
        if (_redo.Count == 0) return;
        var e = _redo.Pop();
        e.Redo();
        _undo.Push(e);
        Changed?.Invoke();
    }

    public void Clear()
    {
        _undo.Clear();
        _redo.Clear();
        Changed?.Invoke();
    }
}
