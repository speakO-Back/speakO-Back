package com.example.speako.domain.evaluation.controller;

import com.example.speako.domain.evaluation.dto.EvaluationResponseDTO;
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
    public ResponseEntity<ApiResponse<EvaluationResponseDTO.ResultDTO>> evaluateRecording(
            @RequestParam("userId") Long userId,
            @RequestParam(value = "presentationId", required = false) Long presentationId,
            @RequestParam(value = "scriptFile", required = false) MultipartFile scriptFile,
            @RequestParam(value = "scriptText", required = false) String scriptText,
            @RequestParam("file") MultipartFile audioFile
    ) {
        try {
            Long targetPresentationId = presentationId;

            if (targetPresentationId == null) {
                if ((scriptFile == null || scriptFile.isEmpty()) && (scriptText == null || scriptText.isBlank())) {
                    throw new IllegalArgumentException("presentationId, 대본 파일, 혹은 텍스트 중 하나는 반드시 제공되어야 합니다.");
                }
                targetPresentationId = evaluationService.createPresentationForCustomScript(userId, scriptFile, scriptText);
            }

            // 1단계: 녹음 파일 S3 저장
            VoiceRecording savedRecording = voiceRecordingService.saveRecording(userId, targetPresentationId, audioFile);

            // 2단계: AI 서버 연동을 통한 평가 결과 생성 및 DTO 반환
            EvaluationResponseDTO.ResultDTO resultDto = evaluationService.evaluateVoice(userId, targetPresentationId, audioFile, savedRecording);

            // 3단계: 최종 결과 반환
            return ResponseEntity.ok(ApiResponse.onSuccess(resultDto));

        } catch (Exception e) {
            e.printStackTrace();
            return ResponseEntity.internalServerError()
                    .body(ApiResponse.onFailure("INTERNAL_SERVER_ERROR", e.getMessage()));
        }
    }

}