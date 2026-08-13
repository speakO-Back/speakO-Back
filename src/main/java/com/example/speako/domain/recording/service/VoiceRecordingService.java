package com.example.speako.domain.recording.service;

import com.example.speako.domain.recording.entity.VoiceRecording;
import com.example.speako.domain.recording.repository.VoiceRecordingRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;

import java.io.File;
import java.io.IOException;
import java.time.LocalDateTime;
import java.util.UUID;

@Service
@RequiredArgsConstructor
public class VoiceRecordingService {
//녹음 파일을 받아 s3에 업로드 처리, url db 저장
    private final VoiceRecordingRepository voiceRecordingRepository;

    @Transactional
    public VoiceRecording saveRecording(Long userId, Long scriptId, MultipartFile file) throws IOException {
        // 1. 파일 이름 생성 및 임시 저장 (또는 S3 업로드 로직 연동)
        String originalFilename = file.getOriginalFilename();
        String savedFileName = UUID.randomUUID() + "_" + originalFilename;
        File dest = new File(System.getProperty("java.io.tmpdir"), savedFileName);
        file.transferTo(dest);

        // 2. S3 저장 경로 URL (실제 S3 업로드 구현체 적용 가능)
        String s3Url = "https://s3.amazonaws.com/speako-recordings/" + savedFileName;

        // 3. voice_recordings 테이블에 녹음 파일 정보 저장
        VoiceRecording recording = VoiceRecording.builder()
                .scriptId(scriptId)
                .userId(userId)
                .audioFileUrl(s3Url)
                .duration(15) // 실제 녹음 시간 측정값 또는 클라이언트 전달값 매핑
                .recordedAt(LocalDateTime.now())
                .build();

        return voiceRecordingRepository.save(recording);
    }
}