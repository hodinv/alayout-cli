package com.alayout.agent;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Context;
import android.os.Bundle;
import android.os.SystemClock;
import android.util.Log;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStreamReader;
import java.util.concurrent.Callable;
import java.util.concurrent.atomic.AtomicReference;

/**
 * The agent alayout runs inside the app being inspected.
 *
 * `am instrument` only starts an Instrumentation in another process when that process belongs to
 * the package named by android:targetPackage, so alayout links this APK for that one app, installs
 * it and restarts the app with the agent attached. The instrumented process keeps running: alayout
 * asks for a dump by writing a line into a file in the app's own data directory (which `run-as`
 * reaches because the app is debuggable), the tree is read on the app's main thread by
 * {@link ComposeDump}, written back next to the request and pulled by alayout. Nothing goes over
 * the network and the app itself is not modified on disk.
 */
public class AlayoutAgent extends Instrumentation {

    private static final String TAG = "alayout";
    private static final long POLL_MS = 150;
    private static final long TOOLING_WAIT_MS = 1200;
    private static final long YIELD_MS = 1500;  // a process with no UI waits this long for one with
    private static final long DEFAULT_TIMEOUT_S = 1800L;

    private Bundle arguments = new Bundle();
    private boolean begun;
    private ComposeDump prepared;

    /** The `-e key value` pairs of the `am instrument` command line.
      *
      * Android is inconsistent about which of these two entry points an Instrumentation gets: the
      * normal path calls onCreate() and then onStart() once the app is bound, several OEM builds
      * stop after onCreate(), and a process that is already running is started into the
      * instrumentation only through onStart(). Starting from both (once) covers them all. */
    @Override
    public void onCreate(Bundle arguments) {
        super.onCreate(arguments);
        this.arguments = arguments == null ? new Bundle() : arguments;
        begin();
    }

    @Override
    public void onStart() {
        super.onStart();
        begin();
    }

    private synchronized void begin() {
        if (begun) {
            return;
        }
        begun = true;
        try {
            startPolling();
        } catch (Throwable e) {
            Log.e(TAG, "agent failed to start: " + e, e);
            finishResult(Activity.RESULT_CANCELED, e.toString());
        }
    }

    private void startPolling() {
        String requestPath = arguments.getString("request");
        if (requestPath == null) {
            finishResult(Activity.RESULT_CANCELED, "-e request <file> is required");
            return;
        }
        Context target = getTargetContext();
        File cache = target == null ? null : target.getCacheDir();
        String out = arguments.getString("out");
        if (out == null) {
            out = new File(cache, "alayout_compose.json").getAbsolutePath();
        }
        final File request = new File(requestPath);
        final File dump = new File(out);
        final boolean debug = "true".equals(arguments.getString("debug"));
        final long deadline = System.currentTimeMillis() + 1000L * seconds();
        Log.i(TAG, "attached to " + target.getPackageName() + ", waiting for " + requestPath);
        Thread poller = new Thread("alayout-agent") {
            @Override
            public void run() {
                waitForRequests(request, dump, deadline, debug);
            }
        };
        poller.setDaemon(true);
        poller.start();
    }

    private long seconds() {
        try {
            String text = arguments.getString("timeout");
            return text == null ? DEFAULT_TIMEOUT_S : Long.parseLong(text.trim());
        } catch (NumberFormatException e) {
            return DEFAULT_TIMEOUT_S;
        }
    }


    /** `<number> <command>` in the request file: dump the tree, or finish. A request number is
      * carried out once, so the host may read the answer at its leisure. */
    private void waitForRequests(File request, File dump, long deadline, boolean debug) {
        Log.i(TAG, "polling " + request + ", writing " + dump);
        String seen = null;
        while (System.currentTimeMillis() < deadline) {
            String line = readLine(request);
            if (line != null && !line.equals(seen)) {
                seen = line;
                String command = commandOf(line);
                final long number = numberOf(line);
                if ("exit".equals(command)) {
                    break;
                }
                if ("prepare".equals(command)) {
                    // turn collection on (and hot-reload once) now, before the user navigates: source
                    // information is filed only on insert, so the screen they open next records its
                    // names as it composes, and no hot reload at capture time resets their navigation
                    prepared = prepareOnMain(debug);
                    SystemClock.sleep(TOOLING_WAIT_MS);
                    String ack = prepared == null
                            ? errorJson(number, "the app did not answer on its main thread")
                            : prepared.ackJson(number);
                    // in a multi-process app the agent also runs in processes with no Compose; let the
                    // one that actually prepared the UI answer, and only fall back to ours if none did
                    answerOrYield(dump, number, ack, ack.contains("\"prepared\":true"), "\"prepared\":true");
                    Log.i(TAG, "prepared (" + ack.length() + " chars)");
                } else if ("dump".equals(command)) {
                    if (prepared == null) {  // a dump without a prepare: enable now (one-shot)
                        prepared = prepareOnMain(debug);
                        SystemClock.sleep(TOOLING_WAIT_MS);
                    }
                    final ComposeDump reader = prepared;
                    final boolean asked = debug;
                    String json = reader == null ? null : onMain(new Callable<String>() {
                        @Override
                        public String call() {
                            return reader.dump(AlayoutAgent.this, number, asked);
                        }
                    });
                    if (reader != null && json != null && reader.sourceInfoSeen() == 0) {
                        // names were not recorded (collection did not take at prepare, e.g. a tile's
                        // composer was enabled after the hot reload): re-enable + hot-reload and retry
                        Log.i(TAG, "no source info; re-enabling tooling and retrying the dump");
                        onMain(new Callable<Boolean>() {
                            @Override
                            public Boolean call() {
                                return reader.refresh(AlayoutAgent.this);
                            }
                        });
                        SystemClock.sleep(TOOLING_WAIT_MS);
                        String retry = onMain(new Callable<String>() {
                            @Override
                            public String call() {
                                return reader.dump(AlayoutAgent.this, number, asked);
                            }
                        });
                        if (retry != null && reader.sourceInfoSeen() > 0) {
                            json = retry;
                        }
                    }
                    if (json == null) {
                        json = errorJson(number, "the app did not answer on its main thread");
                    }
                    answerOrYield(dump, number, json, json.contains("\"windows\":[{"), "\"windows\":[{");
                    Log.i(TAG, "wrote " + dump.getAbsolutePath() + " (" + json.length() + " chars)");
                }
            }
            SystemClock.sleep(POLL_MS);
        }
        finishResult(Activity.RESULT_OK, "alayout agent finished");
    }

    private ComposeDump prepareOnMain(final boolean debug) {
        return onMain(new Callable<ComposeDump>() {
            @Override
            public ComposeDump call() {
                return ComposeDump.prepare(AlayoutAgent.this, debug);
            }
        });
    }

    private <T> T onMain(final Callable<T> task) {
        final AtomicReference<T> result = new AtomicReference<T>();
        try {
            runOnMainSync(new Runnable() {
                @Override
                public void run() {
                    try {
                        result.set(task.call());
                    } catch (Throwable e) {
                        Log.e(TAG, "dump failed: " + e, e);
                    }
                }
            });
        } catch (Throwable e) {
            Log.e(TAG, "the app's main thread did not answer: " + e, e);
        }
        return result.get();
    }

    private void finishResult(int code, String message) {
        Log.i(TAG, message);
        try {
            finish(code, new Bundle());
        } catch (Throwable e) {
            Log.e(TAG, "finish failed: " + e, e);
        }
    }

    private static String readLine(File file) {
        if (!file.canRead()) {
            return null;
        }
        try {
            BufferedReader reader = new BufferedReader(new InputStreamReader(
                    new FileInputStream(file), "UTF-8"));
            try {
                return reader.readLine();
            } finally {
                reader.close();
            }
        } catch (Throwable e) {
            return null;
        }
    }

    /** Write our answer, unless this process has no UI and another one (that does) has already
      * answered this request -- then let that answer stand. We wait a moment for it, and if nobody
      * answers we write ours anyway so the host is never left hanging. `uiNeedle` marks an answer
      * that came from the process holding the Compose UI. */
    private void answerOrYield(File dump, long number, String answer, boolean hasUi, String uiNeedle) {
        if (!hasUi) {
            if (uiAnswered(dump, number, uiNeedle)) {
                Log.i(TAG, "no UI in this process; another process answered request " + number);
                return;
            }
            SystemClock.sleep(YIELD_MS);
            if (uiAnswered(dump, number, uiNeedle)) {
                Log.i(TAG, "no UI in this process; yielded request " + number);
                return;
            }
        }
        write(dump, answer);
    }

    private static boolean uiAnswered(File dump, long number, String uiNeedle) {
        String content = readAll(dump);
        return content != null && content.contains("\"request\":" + number) && content.contains(uiNeedle);
    }

    private static String readAll(File file) {
        if (!file.canRead()) {
            return null;
        }
        try {
            FileInputStream in = new FileInputStream(file);
            try {
                byte[] buffer = new byte[Math.max(1, (int) Math.min(file.length(), 1 << 20))];
                int total = 0, read;
                while (total < buffer.length && (read = in.read(buffer, total, buffer.length - total)) > 0) {
                    total += read;
                }
                return new String(buffer, 0, total, "UTF-8");
            } finally {
                in.close();
            }
        } catch (Throwable e) {
            return null;
        }
    }

    private static void write(File file, String content) {
        try {
            File parent = file.getParentFile();
            if (parent != null) {
                parent.mkdirs();
            }
            FileOutputStream out = new FileOutputStream(file);
            try {
                out.write(content.getBytes("UTF-8"));
                out.flush();
            } finally {
                out.close();
            }
        } catch (Throwable e) {
            Log.e(TAG, "cannot write " + file + ": " + e, e);
        }
    }

    private static String errorJson(long number, String message) {
        return "{\"request\":" + number + ",\"windows\":[],\"errors\":[\"" + message + "\"]}";
    }

    private static long numberOf(String line) {
        String[] parts = line.trim().split("\\s+");
        try {
            return parts.length == 0 ? 0 : Long.parseLong(parts[0]);
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    private static String commandOf(String line) {
        String[] parts = line.trim().split("\\s+");
        return parts.length < 2 ? "" : parts[1];
    }
}
