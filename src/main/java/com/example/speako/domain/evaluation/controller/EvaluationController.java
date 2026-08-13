package com.example.speako.domain.evaluation.controller;

import com.example.speako.domain.evaluation.entity.Evaluation;
import com.example.speako.domain.evaluation.service.EvaluationService;
import com.example.speako.domain.recording.entity.VoiceRecording;
import com.example.speako.domain.recording.service.VoiceRecordingService;
import com.example.speako.global.common.ApiResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

@RestController
@RequestMapping("/api/evaluations")
@RequiredArgsConstructor
public class EvaluationController {

    private final VoiceRecordingService voiceRecordingService;
    private final EvaluationService evaluationService;

    @PostMapping(value = "/record", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ResponseEntity<ApiResponse<Evaluation>> evaluateRecording(
            @RequestParam("userId") Long userId,
            @RequestParam("scriptId") Long scriptId,
            @RequestParam("file") MultipartFile file
    ) {
        try {
            // 1단계: 프론트에서 받은 녹음 파일을 S3에 저장하고 VoiceRecording 엔티티 획득
            VoiceRecording savedRecording = voiceRecordingService.saveRecording(userId, scriptId, file);

            // 2단계: 파이썬 AI 서버 연동을 거쳐 최종 평가 결과(Evaluation) 생성 및 DB 저장
            Evaluation evaluation = evaluationService.evaluateVoice(userId, scriptId, file, savedRecording);

            // 3단계: 최종 결과 반환
            return ResponseEntity.ok(ApiResponse.onSuccess(evaluation));

        } catch (Exception e) {
            e.printStackTrace();
            return ResponseEntity.internalServerError()
                    .body(ApiResponse.onFailure("INTERNAL_SERVER_ERROR", e.getMessage()));
        }
    }
}