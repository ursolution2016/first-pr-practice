package app.stmtcheck;

import android.Manifest;
import android.app.Activity;
import android.app.DownloadManager;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.MediaStore;
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
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import androidx.core.content.FileProvider;

import java.io.File;

/**
 * Thin native shell around the Statement Check web app. The analysis runs on YOUR server; this app only
 * asks for its HTTPS address once, then shows it full-screen with file/camera upload and downloads.
 * Only HTTPS is accepted, navigation stays on the chosen host, and TLS errors are never bypassed.
 */
public class MainActivity extends Activity {
    private static final int REQ_FILE = 1;
    private static final int REQ_STORAGE = 2;
    private static final String PREFS = "stmtcheck";
    private static final String KEY_URL = "server_url";

    private WebView web;
    private LinearLayout setup;
    private EditText urlInput;
    private TextView errorText;
    private String serverBase;   // e.g. https://checker.example.com
    private String serverHost;

    private ValueCallback<Uri[]> pendingChooser;
    private File cameraFile;
    private Uri cameraUri;
    private Runnable afterStoragePermission;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        FrameLayout root = new FrameLayout(this);
        web = new WebView(this);
        web.setVisibility(View.GONE);
        root.addView(web, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setup = buildSetupScreen();
        root.addView(setup, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);
        configureWebView();

        String saved = getSharedPreferences(PREFS, MODE_PRIVATE).getString(KEY_URL, null);
        if (saved != null && applyServer(saved) == null) {
            showWeb();
        } else {
            showSetup(null);
        }
    }

    // ------------------------------------------------------------------ setup screen
    private LinearLayout buildSetupScreen() {
        int pad = (int) (24 * getResources().getDisplayMetrics().density);
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setPadding(pad, pad * 2, pad, pad);
        col.setBackgroundColor(Color.WHITE);

        TextView title = new TextView(this);
        title.setText("Statement Check");
        title.setTextSize(24);
        title.setTextColor(Color.BLACK);
        col.addView(title);

        TextView help = new TextView(this);
        help.setText("Enter the HTTPS address of your Statement Check server. You will be asked for "
                + "its access code on the next screen.");
        help.setPadding(0, pad / 2, 0, pad / 2);
        col.addView(help);

        urlInput = new EditText(this);
        urlInput.setHint("https://checker.example.com");
        urlInput.setSingleLine(true);
        urlInput.setInputType(android.text.InputType.TYPE_CLASS_TEXT
                | android.text.InputType.TYPE_TEXT_VARIATION_URI);
        col.addView(urlInput);

        Button open = new Button(this);
        open.setText("Open");
        open.setOnClickListener(v -> onOpenClicked());
        col.addView(open);

        errorText = new TextView(this);
        errorText.setTextColor(Color.rgb(179, 38, 30));
        errorText.setPadding(0, pad / 2, 0, 0);
        errorText.setGravity(Gravity.START);
        col.addView(errorText);
        return col;
    }

    private void onOpenClicked() {
        String err = applyServer(urlInput.getText().toString());
        if (err != null) {
            errorText.setText(err);
            return;
        }
        getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(KEY_URL, serverBase).apply();
        showWeb();
    }

    /** Validates and stores the server address. Returns an error message, or null when accepted. */
    private String applyServer(String raw) {
        String s = raw == null ? "" : raw.trim();
        if (s.isEmpty()) return "Enter your server address.";
        if (!s.contains("://")) s = "https://" + s;
        if (!s.toLowerCase().startsWith("https://")) {
            return "Only https:// addresses are accepted, because statements are sensitive.";
        }
        Uri u = Uri.parse(s);
        if (u.getHost() == null || u.getHost().isEmpty()) return "That does not look like a web address.";
        serverHost = u.getHost();
        serverBase = "https://" + u.getEncodedAuthority();
        return null;
    }

    private void showSetup(String error) {
        web.setVisibility(View.GONE);
        setup.setVisibility(View.VISIBLE);
        errorText.setText(error == null ? "" : error);
        if (serverBase != null) urlInput.setText(serverBase);
    }

    private void showWeb() {
        setup.setVisibility(View.GONE);
        web.setVisibility(View.VISIBLE);
        web.loadUrl(serverBase + "/");
    }

    // ------------------------------------------------------------------ web view
    private void configureWebView() {
        WebSettings st = web.getSettings();
        st.setJavaScriptEnabled(true);
        st.setDomStorageEnabled(true);
        st.setAllowFileAccess(false);
        st.setAllowContentAccess(true);   // needed to read the picked/captured content:// file
        st.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        st.setUserAgentString(st.getUserAgentString() + " StatementCheckApp/1.0");
        CookieManager.getInstance().setAcceptCookie(true);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri u = request.getUrl();
                if ("https".equals(u.getScheme()) && serverHost != null && serverHost.equalsIgnoreCase(u.getHost())) {
                    return false;   // stay inside the app
                }
                try {   // anything else opens in the browser, never inside the app
                    startActivity(new Intent(Intent.ACTION_VIEW, u));
                } catch (Exception ignored) { }
                return true;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    showSetup("Could not reach " + serverHost + ": " + error.getDescription()
                            + ". Check the address and your connection.");
                }
            }
            // onReceivedSslError is deliberately NOT overridden: invalid certificates are refused.
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

        web.setDownloadListener((url, userAgent, contentDisposition, mimeType, length) -> {
            Uri u = Uri.parse(url);
            if (serverHost == null || !serverHost.equalsIgnoreCase(u.getHost()) || !"https".equals(u.getScheme())) {
                return;   // only download from the configured server
            }
            Runnable go = () -> enqueueDownload(url, userAgent, contentDisposition, mimeType);
            if (Build.VERSION.SDK_INT <= 28
                    && checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED) {
                afterStoragePermission = go;
                requestPermissions(new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE}, REQ_STORAGE);
            } else {
                go.run();
            }
        });
    }

    private void enqueueDownload(String url, String userAgent, String contentDisposition, String mimeType) {
        try {
            String name = URLUtil.guessFileName(url, contentDisposition, mimeType);
            DownloadManager.Request r = new DownloadManager.Request(Uri.parse(url));
            String cookie = CookieManager.getInstance().getCookie(url);   // carries the login session
            if (cookie != null) r.addRequestHeader("Cookie", cookie);
            r.addRequestHeader("User-Agent", userAgent);
            r.setMimeType(mimeType);
            r.setTitle(name);
            r.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            r.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name);
            ((DownloadManager) getSystemService(DOWNLOAD_SERVICE)).enqueue(r);
            Toast.makeText(this, "Downloading " + name, Toast.LENGTH_SHORT).show();
        } catch (Exception e) {
            Toast.makeText(this, "Download failed: " + e.getMessage(), Toast.LENGTH_LONG).show();
        }
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
        menu.add(0, 1, 0, "Reload");
        menu.add(0, 2, 1, "Change server");
        menu.add(0, 3, 2, "Sign out and clear data");
        return true;
    }

    @Override
    public boolean onOptionsItemSelected(MenuItem item) {
        switch (item.getItemId()) {
            case 1:
                if (web.getVisibility() == View.VISIBLE) web.reload();
                return true;
            case 2:
                showSetup(null);
                return true;
            case 3:
                CookieManager.getInstance().removeAllCookies(null);
                web.clearCache(true);
                web.clearHistory();
                Toast.makeText(this, "Signed out", Toast.LENGTH_SHORT).show();
                if (web.getVisibility() == View.VISIBLE) web.loadUrl(serverBase + "/");
                return true;
            default:
                return super.onOptionsItemSelected(item);
        }
    }

    @Override
    public void onBackPressed() {
        if (web.getVisibility() == View.VISIBLE && web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
