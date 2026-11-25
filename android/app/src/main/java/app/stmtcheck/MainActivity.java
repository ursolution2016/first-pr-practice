package app.stmtcheck;

import android.Manifest;
import android.app.Activity;
import android.content.ContentValues;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.MediaStore;
import android.provider.OpenableColumns;
import android.util.Log;
import android.view.Gravity;
import android.view.Menu;
import android.view.MenuItem;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;
import android.widget.Toast;

import androidx.core.content.FileProvider;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.SecureRandom;
import java.util.Locale;

/**
 * Statement Check, self-contained: the analysis engine (Python via Chaquopy) and OCR (ML Kit) run on this
 * phone and statements are never uploaded anywhere. The app starts a private web server on 127.0.0.1 and
 * shows it in a WebView. Files can be picked, photographed, or sent to the app with "Open with" / Share.
 */
public class MainActivity extends Activity {
    private static final String TAG = "StmtCheck";
    private static final int REQ_FILE = 1;
    private static final int REQ_STORAGE = 2;
    private static final long MAX_SHARED_BYTES = 25L * 1024 * 1024;

    private WebView web;
    private TextView status;
    private String base;            // http://127.0.0.1:<port>
    private String token;           // per-launch secret, known only to this app
    private String pendingTarget;   // path (with query) to open once the server is ready

    private ValueCallback<Uri[]> pendingChooser;
    private File cameraFile;
    private Uri cameraUri;
    private Runnable afterStoragePermission;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        FrameLayout root = new FrameLayout(this);
        web = new WebView(this);
        root.addView(web, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        status = new TextView(this);
        status.setText("Starting Statement Check…");
        status.setTextSize(18);
        status.setTextColor(Color.DKGRAY);
        status.setGravity(Gravity.CENTER);
        status.setBackgroundColor(Color.WHITE);
        status.setPadding(48, 48, 48, 48);
        root.addView(status, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);
        configureWebView();

        // Copy any file handed to us before the server finishes starting.
        copySharedFile(getIntent(), target -> pendingTarget = target);

        new Thread(() -> {
            try {
                if (!Python.isStarted()) Python.start(new AndroidPlatform(this));
                PyObject res = Python.getInstance().getModule("stmtcheck_android.server")
                        .callAttr("start", getFilesDir().getAbsolutePath(), getCacheDir().getAbsolutePath());
                int port = res.asList().get(0).toInt();
                token = res.asList().get(1).toString();
                base = "http://127.0.0.1:" + port;
                if ((getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0) {
                    // Debug builds only: lets the CI emulator test drive the app. Never logged in release.
                    Log.i(TAG, "STMTCHECK_READY port=" + port + " token=" + token);
                }
                runOnUiThread(this::openStart);
            } catch (Throwable t) {
                Log.e(TAG, "startup failed", t);
                runOnUiThread(() -> status.setText("Could not start: " + t));
            }
        }, "stmtcheck-start").start();
    }

    private void openStart() {
        String next = pendingTarget != null ? pendingTarget : "/";
        pendingTarget = null;
        web.loadUrl(base + "/_bootstrap/" + token + "?next=" + Uri.encode(next));
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        copySharedFile(intent, target -> {
            if (base != null && token != null) {
                web.loadUrl(base + target);   // already signed in
            } else {
                pendingTarget = target;
            }
        });
    }

    // ------------------------------------------------------------------ shared files
    private interface TargetCallback { void accept(String target); }

    /** Handles ACTION_VIEW / ACTION_SEND: copies the content into the private inbox, off the UI thread. */
    private void copySharedFile(Intent intent, TargetCallback cb) {
        if (intent == null) return;
        Uri uri = null;
        if (Intent.ACTION_VIEW.equals(intent.getAction())) {
            uri = intent.getData();
        } else if (Intent.ACTION_SEND.equals(intent.getAction())) {
            uri = intent.getParcelableExtra(Intent.EXTRA_STREAM);
        }
        if (uri == null) return;
        final Uri src = uri;
        new Thread(() -> {
            try {
                String label = displayName(src);
                String ext = extensionFor(label, getContentResolver().getType(src));
                if (ext == null) {
                    runOnUiThread(() -> Toast.makeText(this, "That file type is not supported.",
                            Toast.LENGTH_LONG).show());
                    return;
                }
                File inbox = new File(getCacheDir(), "inbox");
                //noinspection ResultOfMethodCallIgnored
                inbox.mkdirs();
                byte[] rnd = new byte[8];
                new SecureRandom().nextBytes(rnd);
                StringBuilder name = new StringBuilder();
                for (byte b : rnd) name.append(String.format(Locale.US, "%02x", b));
                File dest = new File(inbox, name + "." + ext);
                long total = 0;
                try (InputStream in = getContentResolver().openInputStream(src);
                     OutputStream out = new FileOutputStream(dest)) {
                    if (in == null) throw new IllegalStateException("cannot open file");
                    byte[] buf = new byte[65536];
                    int n;
                    while ((n = in.read(buf)) > 0) {
                        total += n;
                        if (total > MAX_SHARED_BYTES) throw new IllegalStateException("file is larger than 25 MB");
                        out.write(buf, 0, n);
                    }
                } catch (Exception e) {
                    //noinspection ResultOfMethodCallIgnored
                    dest.delete();
                    throw e;
                }
                String target = "/open-shared?name=" + Uri.encode(dest.getName()) + "&label=" + Uri.encode(label);
                runOnUiThread(() -> cb.accept(target));
            } catch (Exception e) {
                runOnUiThread(() -> Toast.makeText(this, "Could not open that file: " + e.getMessage(),
                        Toast.LENGTH_LONG).show());
            }
        }, "stmtcheck-copy").start();
    }

    private String displayName(Uri uri) {
        try (Cursor c = getContentResolver().query(uri, new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (c != null && c.moveToFirst()) {
                String n = c.getString(0);
                if (n != null && !n.isEmpty()) return n;
            }
        } catch (Exception ignored) { }
        String last = uri.getLastPathSegment();
        return last == null ? "statement" : last;
    }

    private static String extensionFor(String name, String mime) {
        String n = name == null ? "" : name.toLowerCase(Locale.US);
        int dot = n.lastIndexOf('.');
        if (dot >= 0) {
            String ext = n.substring(dot + 1);
            switch (ext) {
                case "pdf": case "png": case "jpg": case "jpeg": case "tif": case "tiff": case "bmp":
                case "webp": case "txt":
                    return ext;
                default:
            }
        }
        if (mime == null) return null;
        switch (mime) {
            case "application/pdf": return "pdf";
            case "image/png": return "png";
            case "image/jpeg": return "jpg";
            case "image/webp": return "webp";
            case "image/tiff": return "tif";
            case "image/bmp": return "bmp";
            case "text/plain": return "txt";
            default: return null;
        }
    }

    // ------------------------------------------------------------------ web view
    private boolean isOurs(Uri u) {
        return base != null && base.equals(u.getScheme() + "://" + u.getHost() + ":" + u.getPort());
    }

    private void configureWebView() {
        WebSettings st = web.getSettings();
        st.setJavaScriptEnabled(true);
        st.setDomStorageEnabled(true);
        st.setAllowFileAccess(false);
        st.setAllowContentAccess(true);   // needed to read the picked/captured content:// file
        st.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        CookieManager.getInstance().setAcceptCookie(true);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri u = request.getUrl();
                if (isOurs(u)) return false;
                try {   // anything else opens in the browser, never inside this app
                    startActivity(new Intent(Intent.ACTION_VIEW, u));
                } catch (Exception ignored) { }
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                if (url != null && base != null && url.startsWith(base)) status.setVisibility(View.GONE);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    status.setVisibility(View.VISIBLE);
                    status.setText("Something went wrong: " + error.getDescription());
                }
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView w, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (pendingChooser != null) pendingChooser.onReceiveValue(null);
                pendingChooser = callback;

                Intent pick = new Intent(Intent.ACTION_GET_CONTENT);
                pick.addCategory(Intent.CATEGORY_OPENABLE);
                pick.setType("*/*");
                pick.putExtra(Intent.EXTRA_MIME_TYPES, new String[]{"application/pdf", "image/*", "text/plain",
                        "application/json"});
                Intent chooser = Intent.createChooser(pick, "Choose a statement");
                Intent camera = buildCameraIntent();
                if (camera != null) chooser.putExtra(Intent.EXTRA_INITIAL_INTENTS, new Intent[]{camera});
                try {
                    startActivityForResult(chooser, REQ_FILE);
                } catch (Exception e) {
                    pendingChooser = null;
                    callback.onReceiveValue(null);
                    return false;
                }
                return true;
            }
        });

        // Exports come from our own local server; save them to Downloads ourselves (the system download
        // manager cannot be relied on to reach loopback or carry the session cookie).
        web.setDownloadListener((url, userAgent, contentDisposition, mimeType, length) -> {
            if (!isOurs(Uri.parse(url))) return;
            Runnable go = () -> saveDownload(url, contentDisposition, mimeType);
            if (Build.VERSION.SDK_INT <= 28
                    && checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED) {
                afterStoragePermission = go;
                requestPermissions(new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE}, REQ_STORAGE);
            } else {
                go.run();
            }
        });
    }

    private void saveDownload(String url, String contentDisposition, String mimeType) {
        final String cookie = CookieManager.getInstance().getCookie(url);
        new Thread(() -> {
            try {
                HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
                if (cookie != null) conn.setRequestProperty("Cookie", cookie);
                if (conn.getResponseCode() != 200) throw new IllegalStateException("HTTP " + conn.getResponseCode());
                String type = mimeType != null && !mimeType.isEmpty() ? mimeType : conn.getContentType();
                String name = URLUtil.guessFileName(url, contentDisposition != null ? contentDisposition
                        : conn.getHeaderField("Content-Disposition"), type);
                OutputStream out;
                if (Build.VERSION.SDK_INT >= 29) {
                    ContentValues cv = new ContentValues();
                    cv.put(MediaStore.Downloads.DISPLAY_NAME, name);
                    cv.put(MediaStore.Downloads.MIME_TYPE, type);
                    cv.put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS);
                    Uri item = getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, cv);
                    if (item == null) throw new IllegalStateException("cannot create file");
                    out = getContentResolver().openOutputStream(item);
                } else {
                    File dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS);
                    //noinspection ResultOfMethodCallIgnored
                    dir.mkdirs();
                    out = new FileOutputStream(new File(dir, name));
                }
                try (InputStream in = conn.getInputStream(); OutputStream o = out) {
                    byte[] buf = new byte[65536];
                    int n;
                    while ((n = in.read(buf)) > 0) o.write(buf, 0, n);
                }
                final String saved = name;
                runOnUiThread(() -> Toast.makeText(this, "Saved to Downloads: " + saved, Toast.LENGTH_LONG).show());
            } catch (Exception e) {
                runOnUiThread(() -> Toast.makeText(this, "Download failed: " + e.getMessage(), Toast.LENGTH_LONG).show());
            }
        }, "stmtcheck-download").start();
    }

    private Intent buildCameraIntent() {
        try {
            File dir = new File(getCacheDir(), "captures");
            //noinspection ResultOfMethodCallIgnored
            dir.mkdirs();
            cameraFile = new File(dir, "statement_" + System.currentTimeMillis() + ".jpg");
            cameraUri = FileProvider.getUriForFile(this, getPackageName() + ".files", cameraFile);
            Intent i = new Intent(MediaStore.ACTION_IMAGE_CAPTURE);
            i.putExtra(MediaStore.EXTRA_OUTPUT, cameraUri);
            i.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION | Intent.FLAG_GRANT_READ_URI_PERMISSION);
            return i;
        } catch (Exception e) {
            cameraFile = null;
            cameraUri = null;
            return null;
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != REQ_FILE || pendingChooser == null) return;
        Uri[] results = null;
        if (resultCode == RESULT_OK) {
            if (data != null && data.getData() != null) {
                results = new Uri[]{data.getData()};
            } else if (cameraFile != null && cameraFile.length() > 0) {
                results = new Uri[]{cameraUri};
            }
        }
        pendingChooser.onReceiveValue(results);
        pendingChooser = null;
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQ_STORAGE) {
            Runnable r = afterStoragePermission;
            afterStoragePermission = null;
            if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED && r != null) {
                r.run();
            } else {
                Toast.makeText(this, "Storage permission is needed to save the file.", Toast.LENGTH_LONG).show();
            }
        }
    }

    // ------------------------------------------------------------------ menu / back
    @Override
    public boolean onCreateOptionsMenu(Menu menu) {
        menu.add(0, 1, 0, "Home");
        menu.add(0, 2, 1, "Reload");
        return true;
    }

    @Override
    public boolean onOptionsItemSelected(MenuItem item) {
        if (base == null) return true;
        if (item.getItemId() == 1) {
            web.loadUrl(base + "/");
            return true;
        }
        if (item.getItemId() == 2) {
            web.reload();
            return true;
        }
        return super.onOptionsItemSelected(item);
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
