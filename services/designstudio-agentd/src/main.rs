use designstudio_agentd::ControlPlane;
use std::env;
use std::io::{self, BufRead, BufReader, Write};

fn serve<R: BufRead, W: Write>(reader: R, mut writer: W) -> io::Result<()> {
    let mut control_plane = ControlPlane::default();
    for line in reader.lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        writeln!(writer, "{}", control_plane.handle_json(&line))?;
        writer.flush()?;
    }
    Ok(())
}

#[cfg(unix)]
fn serve_socket(path: &str) -> io::Result<()> {
    use std::fs;
    use std::os::unix::fs::PermissionsExt;
    use std::os::unix::net::UnixListener;
    use std::path::Path;

    let socket = Path::new(path);
    if socket.exists() {
        fs::remove_file(socket)?;
    }
    let listener = UnixListener::bind(socket)?;
    fs::set_permissions(socket, fs::Permissions::from_mode(0o600))?;
    let result = (|| {
        for stream in listener.incoming() {
            let stream = stream?;
            let reader = BufReader::new(stream.try_clone()?);
            serve(reader, stream)?;
        }
        Ok(())
    })();
    let _ = fs::remove_file(socket);
    result
}

fn main() -> io::Result<()> {
    let args: Vec<String> = env::args().collect();
    if args.len() == 1 || (args.len() == 2 && args[1] == "--stdio") {
        return serve(io::stdin().lock(), io::stdout().lock());
    }
    match args.as_slice() {
        #[cfg(unix)]
        [_, flag, path] if flag == "--socket" => serve_socket(path),
        _ => {
            eprintln!("usage: designstudio-agentd [--stdio | --socket PATH]");
            std::process::exit(2);
        }
    }
}
