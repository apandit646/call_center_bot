import { useEffect, useMemo, useState } from "react";
import { useAudioPlayer, useAudioPlayerStatus } from "expo-audio";
import { api, server, tokens } from "@/api/client";
import { Button, ErrorBox, Row, T } from "./ui";

const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;

function Player({ uri }: { uri: string }) {
  // The recording is behind the login, so the request carries the same token as every API call.
  const source = useMemo(() => ({ uri, headers: { Authorization: `Bearer ${tokens.access}` } }), [uri]);
  const player = useAudioPlayer(source);
  const status = useAudioPlayerStatus(player);

  useEffect(() => {
    player.play();
  }, [player]);

  const toggle = () => {
    if (status.playing) return player.pause();
    if (status.duration && status.currentTime >= status.duration - 0.25) void player.seekTo(0); // finished: play again
    player.play();
  };

  return (
    <Row>
      <Button small variant="ghost" title={status.playing ? "Pause" : "Play"} onPress={toggle} />
      <T small muted>
        {status.isLoaded ? `${clock(status.currentTime)} / ${clock(status.duration)}` : "Loading the recording…"}
      </T>
    </Row>
  );
}

/** Plays a call recording from the API, e.g. path "/calls/{id}/recording". Nothing is downloaded until Play is pressed. */
export function RecordingPlayer({ path }: { path: string }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.get("/auth/me"); // renews the login token first if it has expired; the player cannot do that itself
      setOpen(true);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  if (open) return <Player uri={`${server.apiUrl}${path}`} />;
  return (
    <>
      <ErrorBox error={error} />
      <Row>
        <Button small variant="ghost" title="Play recording" busy={busy} onPress={start} />
      </Row>
    </>
  );
}
