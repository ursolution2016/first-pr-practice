package app.stmtcheck;

import android.graphics.Bitmap;
import android.graphics.Color;
import android.graphics.pdf.PdfRenderer;
import android.os.ParcelFileDescriptor;

import org.json.JSONArray;

import java.io.File;
import java.io.FileOutputStream;

/** Renders PDF pages to PNG files with the platform PdfRenderer. Called from Python. */
public final class PdfBridge {
    private static final long MAX_PIXELS = 12_000_000L;   // keeps memory sane on phones

    private PdfBridge() { }

    /** Returns a JSON array of PNG paths, one per page, written into outDir. */
    public static String render(String pdfPath, int dpi, String outDir) throws Exception {
        JSONArray names = new JSONArray();
        try (ParcelFileDescriptor pfd = ParcelFileDescriptor.open(new File(pdfPath), ParcelFileDescriptor.MODE_READ_ONLY)) {
            PdfRenderer renderer = new PdfRenderer(pfd);
            try {
                for (int i = 0; i < renderer.getPageCount(); i++) {
                    PdfRenderer.Page page = renderer.openPage(i);
                    try {
                        double scale = dpi / 72.0;
                        double px = page.getWidth() * scale * page.getHeight() * scale;
                        if (px > MAX_PIXELS) scale *= Math.sqrt(MAX_PIXELS / px);
                        int w = Math.max(1, (int) Math.round(page.getWidth() * scale));
                        int h = Math.max(1, (int) Math.round(page.getHeight() * scale));
                        Bitmap bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
                        bmp.eraseColor(Color.WHITE);
                        page.render(bmp, null, null, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY);
                        File out = new File(outDir, "page" + i + ".png");
                        try (FileOutputStream fos = new FileOutputStream(out)) {
                            bmp.compress(Bitmap.CompressFormat.PNG, 100, fos);
                        }
                        bmp.recycle();
                        names.put(out.getAbsolutePath());
                    } finally {
                        page.close();
                    }
                }
            } finally {
                renderer.close();
            }
        }
        return names.toString();
    }
}
