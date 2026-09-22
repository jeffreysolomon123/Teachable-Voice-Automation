package com.uirecorder.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import com.uirecorder.app.ui.ActionCaptureScreen
import com.uirecorder.app.ui.RecorderScreen
import com.uirecorder.app.ui.RecorderViewModel
import com.uirecorder.app.ui.TapReviewScreen
import com.uirecorder.app.ui.theme.UiActionRecorderTheme

private enum class Screen { RECORDER, TAP_REVIEW, ACTION_CAPTURE }

class MainActivity : ComponentActivity() {

    private val viewModel: RecorderViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            UiActionRecorderTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background
                ) {
                    var screen by rememberSaveable { mutableStateOf(Screen.RECORDER) }
                    when (screen) {
                        Screen.TAP_REVIEW -> TapReviewScreen(viewModel = viewModel, onBack = { screen = Screen.RECORDER })
                        Screen.ACTION_CAPTURE -> ActionCaptureScreen(viewModel = viewModel, onBack = { screen = Screen.RECORDER })
                        Screen.RECORDER -> RecorderScreen(
                            viewModel = viewModel,
                            onOpenReview = { screen = Screen.TAP_REVIEW },
                            onOpenActionCapture = { screen = Screen.ACTION_CAPTURE }
                        )
                    }
                }
            }
        }
    }
}
