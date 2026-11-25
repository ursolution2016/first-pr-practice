package app.stmtcheck;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Rect;

import com.google.android.gms.tasks.Tasks;
import com.google.mlkit.vision.common.InputImage;
import com.google.mlkit.vision.text.Text;
import com.google.mlkit.vision.text.TextRecognition;
import com.google.mlkit.vision.text.TextRecognizer;
import com.google.mlkit.vision.text.latin.TextRecognizerOptions;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * On-device OCR (Google ML Kit, bundled model, works offline). Called from Python by
 * stmtcheck.androidbridge. Must NOT be called on the main thread (it blocks on the recognizer task);
 * the Python web server runs on its own threads.
 */
public final class OcrBridge {
    private OcrBridge() { }

    /** Returns a JSON array of words: {text, x0, x1, top, bottom, conf (0-100)}. */
    public static String recognize(String pngPath) throws Exception {
        Bitmap bitmap = BitmapFactory.decodeFile(pngPath);
        if (bitmap == null) throw new IllegalArgumentException("Cannot decode image");
        TextRecognizer recognizer = TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS);
        try {
            Text text = Tasks.await(recognizer.process(InputImage.fromBitmap(bitmap, 0)));
            JSONArray out = new JSONArray();
            for (Text.TextBlock block : text.getTextBlocks()) {
                for (Text.Line line : block.getLines()) {
                    for (Text.Element el : line.getElements()) {
                        Rect r = el.getBoundingBox();
                        if (r == null) continue;
                        JSONObject o = new JSONObject();
                        o.put("text", el.getText());
                        o.put("x0", r.left);
                        o.put("x1", r.right);
                        o.put("top", r.top);
                        o.put("bottom", r.bottom);
                        o.put("conf", Math.round(el.getConfidence() * 1000) / 10.0);
                        out.put(o);
                    }
                }
            }
            return out.toString();
        } finally {
            recognizer.close();
            bitmap.recycle();
        }
    }
}
